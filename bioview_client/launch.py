"""BioView entry point.

A single multi-call binary. ``--role`` selects what to run:

- ``monitor`` (default): the Monitor GUI.
- ``configurator``: the Configurator GUI.
- ``server``: the headless server, used only as the child process the GUI roles
  spawn for themselves.

Both GUI roles always come up against a localhost server, started here in a
*separate* OS process so UHD and PyQt never share one interpreter and GIL. The
server is shared: the first window starts it, every later window reuses it, and
it only goes away once the last window has closed.
"""

import argparse
import atexit
import contextlib
import os
import socket
import subprocess
import sys
import threading
import time
import uuid

from bioview_common import (
    CONTROL_PORT,
    DATA_PORT,
    Command,
    Response,
    get_cache_file,
    parse_and_validate_response,
    send_command,
)


CLIENT_ROLES = ("monitor", "configurator")


class ServerStartupError(RuntimeError):
    """No server could be started, so there is no point opening a window."""


# Windows flag to start the child server without flashing a console window.
_CREATE_NO_WINDOW = 0x08000000

# Seconds with no client *and no window* before a spawned server retires
# itself. This is not a deadline for the window to connect by: a live window
# keeps its server alive with the heartbeat below however long it takes to
# connect, so the timeout only has to outlast one window closing and another
# opening.
SERVER_IDLE_TIMEOUT = 20

# How often a window tells its server that it is still there. The server
# restarts its idle countdown on every local discovery probe, so this only has
# to be comfortably shorter than SERVER_IDLE_TIMEOUT.
SERVER_HEARTBEAT_S = 5.0


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


#: Identifies this window process to its server, for as long as it runs.
#: Opaque and per-process: the server only ever counts these, never interprets
#: them, and a new window is a new token.
_WINDOW_TOKEN = uuid.uuid4().hex

#: Which window this is. Carried with the claim purely so ``server.log`` can
#: say who is holding a server open, which is the first thing anyone wants to
#: know when one outlives -- or does not outlive -- its windows.
_WINDOW_ROLE = {"role": "window"}


def _claim_params(leaving: bool = False) -> dict:
    """This window's claim on the server it is about to talk to."""
    return {
        "window": _WINDOW_TOKEN,
        "role": _WINDOW_ROLE["role"],
        "heartbeat": SERVER_HEARTBEAT_S,
        "leaving": leaving,
    }


def _server_info(
    host: str = "127.0.0.1",
    port: int = CONTROL_PORT,
    timeout: float = 1.0,
    claim: bool = True,
    leaving: bool = False,
):
    """Ask the server on this port to describe itself, or None if none answers.

    By default the question doubles as this window's claim on that server; the
    reply says how many windows and clients it has, this one included. Pass
    ``claim=False`` to ask without claiming, or ``leaving=True`` to withdraw
    the claim -- the reply then counts everyone *else*.
    """
    params = _claim_params(leaving) if claim else None
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            response = send_command(
                sock=sock, command=Command.DISCOVER_SERVERS, params=params
            )
    except OSError:
        return None

    resp_type, payload = parse_and_validate_response(response)
    if resp_type != Response.SUCCESS.name:
        return None
    return payload or {}


def _server_running(
    host: str = "127.0.0.1", port: int = CONTROL_PORT, timeout: float = 1.0
) -> bool:
    """Return True if a *BioView* server is already answering on the control port.

    This speaks the discovery handshake rather than only completing a TCP
    connect: any unrelated process holding the port would satisfy a bare
    connect, and we would then wait forever for a server that is never coming.
    """
    return _server_info(host, port, timeout) is not None


# The heartbeat that keeps this window's server alive. Module state, because
# there is exactly one server per window process.
_heartbeat = {"stop": None, "thread": None}

# The server child this window is responsible for, if it started one. Tracked
# here rather than only being returned, because a restart replaces it and
# whoever shuts the window down has to release the *current* one.
_server_child = {"proc": None}


def _start_heartbeat(control_port: int = CONTROL_PORT) -> None:
    """Claim the shared server for as long as this window process lives.

    The server retires itself when it has been idle, and "idle" cannot mean
    "no authenticated client": a window is a client of its server long before
    it manages to connect. The Monitor in particular builds its client only
    after the configuration dialog has been answered (see
    ``BioViewMonitor.__init__``), and that dialog can sit open indefinitely --
    nobody is obliged to answer it. A longer timeout would only move that
    cliff, not remove it.

    So the claim is tied to process liveness instead of to a duration: an
    ordinary daemon thread, outside Qt entirely, so a blocked event loop cannot
    stall it, and so it dies exactly when this process does. The server is then
    only ever retired once no window is left to speak for it.
    """
    if _heartbeat["thread"] is not None:
        return

    stop = threading.Event()

    def _beat():
        while not stop.wait(SERVER_HEARTBEAT_S):
            # Nothing here may raise: a heartbeat thread that dies takes this
            # window's claim with it, and the server would retire underneath a
            # window that is still very much open.
            with contextlib.suppress(Exception):
                _server_info(port=control_port, timeout=1.0)

    thread = threading.Thread(target=_beat, name="bioview-server-heartbeat", daemon=True)
    _heartbeat["stop"] = stop
    _heartbeat["thread"] = thread
    thread.start()


def _stop_heartbeat() -> None:
    """Give up the claim. Called before a window decides the server is unused."""
    stop = _heartbeat["stop"]
    if stop is not None:
        stop.set()
    _heartbeat["stop"] = None
    _heartbeat["thread"] = None


def server_log_path():
    """Where a GUI-spawned server writes its log.

    The server is a detached child process, so its log is the only record of
    what happened on the device side, and the user has to be able to find it.
    """
    return get_cache_file("server.log")


def _spawn_server(control_port: int, data_port: int) -> subprocess.Popen:
    """Start a hidden, local-only server as a child process."""
    server_args = [
        "--local",
        "--control-port",
        str(control_port),
        "--data-port",
        str(data_port),
        "--exit-when-idle",
        str(SERVER_IDLE_TIMEOUT),
    ]
    if _is_frozen():
        # sys.executable is the bundled app, so re-exec it in the server role.
        cmd = [sys.executable, "--role", "server", *server_args]
    else:
        cmd = [sys.executable, "-m", "bioview_server.server", *server_args]

    # A windowed GUI build may have no valid console, so the child's output
    # cannot be inherited. Send it to a log file rather than discarding it.
    log_handle = None
    with contextlib.suppress(Exception):
        log_path = server_log_path()
        # Not a context manager: the handle is passed to Popen as the child's
        # stdout and has to stay open for the life of the server process.
        log_handle = open(  # noqa: SIM115
            log_path, "a", buffering=1, encoding="utf-8", errors="replace"
        )
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_handle.write(f"\n===== BioView server started {stamp} =====\n")

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle or subprocess.DEVNULL,
        "stderr": subprocess.STDOUT if log_handle else subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW

    return subprocess.Popen(cmd, **kwargs)


def _release_server(child: subprocess.Popen, control_port: int = CONTROL_PORT) -> None:
    """Give up this window's claim on the server, shutting it down if we are last.

    The withdrawal and the question are one exchange: the server drops this
    window's claim and answers with what is left. Two counts have to be zero
    before anything is killed -- the other windows still claiming it, which
    covers a window that is starting up and has not connected yet, and the
    connected clients, which covers anyone this window never knew about.

    If the answer is unavailable, nothing is killed. The server was spawned
    with --exit-when-idle, so it retires on its own once it really has been
    abandoned; a failed probe must never take a server out from under a window
    that is still using it.
    """
    # Stopped first: this window must not go on claiming, in the background,
    # the very server it is about to decide nobody is using.
    _stop_heartbeat()

    info = _server_info(port=control_port, leaving=True)

    # A restart during the session replaces the child this window first spawned.
    child = _server_child["proc"] or child
    _server_child["proc"] = None

    if child is None or child.poll() is not None:
        return

    if info is None or info.get("clients") or info.get("windows"):
        return

    _terminate(child)


def _terminate(child: subprocess.Popen, timeout: float = 5.0) -> None:
    if child is None or child.poll() is not None:
        return
    with contextlib.suppress(Exception):
        child.terminate()
    try:
        child.wait(timeout=timeout)
    except Exception:
        with contextlib.suppress(Exception):
            child.kill()


def _wait_for_server(control_port: int, timeout: float) -> bool:
    """Poll until a BioView server answers on the control port, or time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _server_running(port=control_port):
            return True
        time.sleep(0.1)
    return False


def _ensure_server(control_port: int, data_port: int, role: str = "window"):
    """Make sure exactly one localhost server is running, and say whether it is ours.

    Returns the child process when this launch started the server (the caller
    releases it on exit), or None when an existing server was reused.
    """
    _WINDOW_ROLE["role"] = role
    _start_heartbeat(control_port)

    # This probe carries the claim as well as the question, which is what makes
    # reuse safe: a server a fraction of a second from its idle exit is held
    # open by the very question that found it, rather than vanishing while this
    # window is still building itself.
    if _server_running(port=control_port):
        return None

    child = _spawn_server(control_port, data_port)

    # Two windows opened at the same moment can both get this far; only one of
    # them wins the port, and the loser's child exits on the bind error.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _server_running(port=control_port):
            break
        if child.poll() is not None:
            break
        time.sleep(0.1)

    if child.poll() is not None:
        # Our child is gone: it lost the race for the port, or it failed to
        # start. Only the first of those leaves a server behind, so the answer
        # decides between "not ours to shut down" and "nobody is coming".
        if _wait_for_server(control_port, timeout=3.0):
            return None

        # One retry: the common cause of a lone child dying is a race whose
        # winner has since retired, and a second attempt then simply works.
        child = _spawn_server(control_port, data_port)
        if not _wait_for_server(control_port, timeout=10.0):
            _terminate(child)
            _stop_heartbeat()
            raise ServerStartupError(
                "BioView could not start its local server on port "
                f"{control_port}. See {server_log_path()} for why."
            )

    _server_child["proc"] = child
    atexit.register(_release_server, child, control_port)
    return child


def restart_server(control_port: int = CONTROL_PORT, data_port: int = DATA_PORT) -> None:
    """Bring a server back after the one this window was using went away.

    Unlike the startup path there is no window to hold back: the GUI is already
    open and this is a deliberate, user-initiated retry, so it either works or
    it says why. Raises ServerStartupError if no server is answering when it
    returns.
    """
    _start_heartbeat(control_port)

    # Somebody may have got there first -- another window noticing the same
    # death -- in which case there is nothing to do but reuse what is there.
    if _server_running(port=control_port):
        return

    child = _spawn_server(control_port, data_port)
    if not _wait_for_server(control_port, timeout=10.0):
        _terminate(child)
        raise ServerStartupError(
            f"The server did not start on port {control_port}. "
            f"See {server_log_path()} for why."
        )

    previous = _server_child["proc"]
    _server_child["proc"] = child
    if previous is not None and previous is not child:
        _terminate(previous)


def run_server(control_port: int, data_port: int, rest) -> int:
    """Run the headless server in this process."""
    from bioview_server.server import main as server_main

    server_argv = [
        "--control-port",
        str(control_port),
        "--data-port",
        str(data_port),
        *rest,
    ]
    return server_main(server_argv) or 0


def _report_startup_failure(message: str) -> None:
    """Tell the user the server never came up, on stderr and in a dialog."""
    print(message, file=sys.stderr)

    with contextlib.suppress(Exception):
        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "BioView could not start", message)
        del app


def run_client(role: str, control_port: int, data_port: int, rest) -> int:
    """Open one client window against a shared localhost server."""
    try:
        child = _ensure_server(control_port, data_port, role=role)
    except ServerStartupError as e:
        # A GUI build has no console to print to, and a window that can never
        # connect is worse than no window: say so, and point at the log.
        _report_startup_failure(str(e))
        return 1

    if role == "configurator":
        from bioview_client.configurator import run_configurator as run_gui
    else:
        from bioview_client.monitor import run_monitor as run_gui

    try:
        # The ports are parsed here, so they have to be handed on: a window
        # left to its own defaults would look for its server on 8998 while the
        # launcher had just started one somewhere else entirely.
        return run_gui(rest, control_port=control_port, data_port=data_port) or 0
    finally:
        _release_server(child, control_port)


def main_monitor(argv=None) -> int:
    """Console-script entry point for the Monitor."""
    return main(["--role", "monitor", *(sys.argv[1:] if argv is None else argv)])


def main_configurator(argv=None) -> int:
    """Console-script entry point for the Configurator."""
    return main(["--role", "configurator", *(sys.argv[1:] if argv is None else argv)])


def main(argv=None) -> int:
    import multiprocessing as mp

    # Required so the frozen binary does not re-launch the GUI when spawning
    # child processes under PyInstaller.
    mp.freeze_support()

    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="bioview",
        description="BioView launcher (opens a client window and its server)",
        add_help=False,
    )
    parser.add_argument("--role", choices=[*CLIENT_ROLES, "server"], default="monitor")
    parser.add_argument("--control-port", type=int, default=CONTROL_PORT)
    parser.add_argument("--data-port", type=int, default=DATA_PORT)
    parser.add_argument(
        "-h", "--help", action="store_true", help="Show this help message and exit"
    )
    args, rest = parser.parse_known_args(argv)

    if args.help:
        parser.print_help()
        return 0

    if args.role == "server":
        return run_server(args.control_port, args.data_port, rest)

    return run_client(args.role, args.control_port, args.data_port, rest)


if __name__ == "__main__":
    sys.exit(main())

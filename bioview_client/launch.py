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
import json
import os
import socket
import subprocess
import sys
import time

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

# Windows flag to start the child server without flashing a console window.
_CREATE_NO_WINDOW = 0x08000000

# Seconds with no client before a spawned server retires itself. Long enough
# to cover one window closing and another opening.
SERVER_IDLE_TIMEOUT = 20


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# Window registry: a shared server is only shut down once no other window is
# registered against its port. See bioview-docs/architecture/launching.md.

_REGISTRY_FILE = "windows.json"

#: A lock left behind by a process that died holding it is broken after this
#: long. It is only ever held for a couple of file operations.
_LOCK_STALE_S = 10.0


def _registry_path():
    return get_cache_file(_REGISTRY_FILE)


@contextlib.contextmanager
def _registry_lock(timeout: float = 2.0):
    """Serialize registry updates across processes with an exclusive lock file."""
    lock_path = str(_registry_path()) + ".lock"
    deadline = time.monotonic() + timeout
    fd = None

    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(lock_path) > _LOCK_STALE_S:
                    os.unlink(lock_path)
                    continue
            if time.monotonic() > deadline:
                # Never block a window from opening on a lock we cannot take.
                yield
                return
            time.sleep(0.05)
        except OSError:
            yield
            return

    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(lock_path)


def _pid_alive(pid: int) -> bool:
    """True if a process with this pid is still running.

    ``os.kill(pid, 0)`` is not usable on Windows -- CPython implements it with
    TerminateProcess, so it would kill the very window we are asking about.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process, but it exists.
        return True
    return True


def _read_registry():
    try:
        raw = _registry_path().read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        return []
    return entries if isinstance(entries, list) else []


def _write_registry(entries) -> None:
    with contextlib.suppress(OSError):
        _registry_path().write_text(json.dumps(entries), encoding="utf-8")


def _prune(entries):
    """Drop entries whose window is gone (a crash leaves one behind)."""
    return [e for e in entries if isinstance(e, dict) and _pid_alive(e.get("pid"))]


def _register_window(role: str, control_port: int) -> None:
    """Record this process as a live BioView window using ``control_port``."""
    with _registry_lock():
        entries = [e for e in _prune(_read_registry()) if e.get("pid") != os.getpid()]
        entries.append({"pid": os.getpid(), "role": role, "port": int(control_port)})
        _write_registry(entries)

    atexit.register(_unregister_window)


def _unregister_window() -> None:
    """Remove this process from the registry. Safe to call more than once."""
    with _registry_lock():
        entries = [e for e in _prune(_read_registry()) if e.get("pid") != os.getpid()]
        _write_registry(entries)


def _other_windows_open(control_port: int) -> bool:
    """True if a BioView window other than this one is using this server."""
    with _registry_lock():
        entries = _prune(_read_registry())
        _write_registry(entries)

    return any(
        e.get("pid") != os.getpid()
        and int(e.get("port", CONTROL_PORT)) == int(control_port)
        for e in entries
    )


def _server_info(
    host: str = "127.0.0.1", port: int = CONTROL_PORT, timeout: float = 1.0
):
    """Ask the server on this port to describe itself, or None if none answers."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            response = send_command(sock=sock, command=Command.DISCOVER_SERVERS)
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

    Three things are checked, in order of how much they can be trusted:

    1. This window deregisters itself, then asks whether any other window is
       registered against this port. That covers a window which is still
       starting up and has not connected yet -- a client count cannot.
    2. The server's own client count, as a second opinion.
    3. If either answer is unavailable, nothing is killed. The server was
       spawned with --exit-when-idle, so it retires on its own once it really
       has been abandoned; a failed probe must never take a server out from
       under a window that is still using it.
    """
    _unregister_window()

    if child is None or child.poll() is not None:
        return

    if _other_windows_open(control_port):
        return

    info = _server_info(port=control_port)
    if info is None or info.get("clients"):
        # Unknown, or somebody is still connected: leave it to the idle timeout.
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
    # Registered before anything else: a window that is still starting up is
    # already a reason to keep the shared server alive.
    _register_window(role, control_port)

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
        # Our child is gone: it lost the race, or failed to start. Either way it
        # is not ours to shut down.
        _wait_for_server(control_port, timeout=3.0)
        return None

    atexit.register(_release_server, child, control_port)
    return child


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


def run_client(role: str, control_port: int, data_port: int, rest) -> int:
    """Open one client window against a shared localhost server."""
    child = _ensure_server(control_port, data_port, role=role)

    if role == "configurator":
        from bioview_client.configurator import run_configurator as run_gui
    else:
        from bioview_client.monitor import run_monitor as run_gui

    try:
        return run_gui(rest) or 0
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

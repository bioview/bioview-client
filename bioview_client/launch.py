"""BioView unified launcher.

A single multi-call entry point used by both source installs and the frozen
single-binary bundles. Selecting a ``--role`` dispatches to one of:

- ``launcher`` (default): start a hidden localhost server in a *separate* OS
  process -- so UHD and PyQt never share one interpreter/GIL -- then open the
  Monitor GUI, and terminate the server again on exit.
- ``server``: run the headless BioView server (used both directly and as the
  child process spawned by the launcher).
- ``monitor``: run only the Monitor GUI (no embedded server).
- ``configurator``: run the Configurator GUI.

The same binary is reused for the child server: when frozen, ``sys.executable``
is the bundled app, so we re-exec it with ``--role server``; from source we run
``python -m bioview_server.server`` instead (a soft dependency -- the client
never imports the server package at module load time).
"""

import argparse
import atexit
import contextlib
import os
import socket
import subprocess
import sys
import time

from bioview_common import CONTROL_PORT, DATA_PORT

# Windows flag to start the child server without flashing a console window.
_CREATE_NO_WINDOW = 0x08000000


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _server_running(host: str = "127.0.0.1", port: int = CONTROL_PORT,
                    timeout: float = 0.25) -> bool:
    """Return True if something is already accepting connections on the control
    port (an existing localhost server we should reuse rather than respawn)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _spawn_server(control_port: int, data_port: int) -> subprocess.Popen:
    """Start a hidden, local-only server as a child process."""
    if _is_frozen():
        cmd = [
            sys.executable, "--role", "server", "--local",
            "--control-port", str(control_port),
            "--data-port", str(data_port),
        ]
    else:
        cmd = [
            sys.executable, "-m", "bioview_server.server", "--local",
            "--control-port", str(control_port),
            "--data-port", str(data_port),
        ]

    kwargs = {
        # A windowed GUI build may have no valid console; detach the child's std
        # streams so the server's logging never breaks on an invalid handle.
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW

    return subprocess.Popen(cmd, **kwargs)


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


def run_server(control_port: int, data_port: int, rest) -> int:
    """Run the headless server in this process."""
    from bioview_server.server import main as server_main

    server_argv = [
        "--control-port", str(control_port),
        "--data-port", str(data_port),
        *rest,
    ]
    return server_main(server_argv) or 0


def run_launcher(control_port: int, data_port: int, rest) -> int:
    """Ensure a localhost server exists, then run the Monitor GUI."""
    child = None
    if not _server_running(port=control_port):
        child = _spawn_server(control_port, data_port)
        atexit.register(_terminate, child)

        # Give the freshly spawned server a brief head start to bind its sockets.
        # The monitor also retries localhost autoconnect, so this is best-effort.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not _server_running(port=control_port):
            if child.poll() is not None:
                break  # child exited early (e.g. port already taken)
            time.sleep(0.1)

    from bioview_client.monitor import run_monitor

    try:
        return run_monitor(rest)
    finally:
        _terminate(child)


def main(argv=None) -> int:
    import multiprocessing as mp

    # Required so the frozen binary does not re-launch the GUI when spawning
    # child processes under PyInstaller.
    mp.freeze_support()

    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="bioview",
        description="BioView launcher (server + GUI orchestration)",
        add_help=False,
    )
    parser.add_argument(
        "--role",
        choices=["launcher", "server", "monitor", "configurator"],
        default="launcher",
    )
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

    if args.role == "configurator":
        from bioview_client.configurator import main as configurator_main

        return configurator_main(rest) or 0

    if args.role == "monitor":
        from bioview_client.monitor import run_monitor

        return run_monitor(rest)

    return run_launcher(args.control_port, args.data_port, rest)


if __name__ == "__main__":
    sys.exit(main())

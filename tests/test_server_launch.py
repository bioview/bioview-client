"""Exactly one BioView server per machine, shared by every window.

The launcher starts a server only when none is running, so a Monitor opened
alongside a Configurator reuses the server that is already there, and the server
itself refuses to start a second time on the same ports.
"""
import socket
import sys
import threading
import time
import types

import pytest
from bioview_common import CONTROL_PORT
from bioview_server.server import Server

from bioview_client import launch


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def running_server():
    control_port, data_port = _free_port(), _free_port()
    srv = Server(local_only=True, control_port=control_port, data_port=data_port)
    thread = threading.Thread(target=srv.start, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not launch._server_running(port=control_port):
        time.sleep(0.05)
    else_started = launch._server_running(port=control_port)
    if not else_started:
        srv.stop()
        pytest.fail("server did not start in time")

    yield srv, control_port, data_port

    srv.stop()
    thread.join(timeout=5)


def test_a_second_server_refuses_to_bind_the_same_ports(running_server):
    """Windows SO_REUSEADDR would happily bind a port that is already being
    listened on, leaving two servers up and splitting clients between them."""
    _, control_port, data_port = running_server
    duplicate = Server(local_only=True, control_port=control_port, data_port=data_port)
    with pytest.raises(OSError):
        duplicate._create_sockets()


def test_an_existing_server_is_reused_rather_than_replaced(running_server):
    _, control_port, data_port = running_server
    # None means "not ours, we reused what was already running".
    assert launch._ensure_server(control_port, data_port) is None


def test_a_port_held_by_something_else_is_not_mistaken_for_a_server():
    """A bare TCP connect would call any listener a BioView server, and the GUI
    would then wait forever for a server nobody ever started."""
    impostor = socket.socket()
    impostor.bind(("127.0.0.1", 0))
    impostor.listen(1)
    port = impostor.getsockname()[1]
    try:
        assert not launch._server_running(port=port)
    finally:
        impostor.close()


def test_no_server_at_all_is_reported_as_not_running():
    assert not launch._server_running(port=_free_port())


@pytest.mark.parametrize(
    ("entry_point", "role"),
    [("main_monitor", "monitor"), ("main_configurator", "configurator")],
)
def test_every_client_entry_point_starts_a_server(monkeypatch, entry_point, role):
    """Both windows need a server: the Monitor to stream, the Configurator to
    list attached hardware. Neither may open with nothing to connect to."""
    calls = {}

    monkeypatch.setattr(
        launch,
        "run_client",
        lambda role, cp, dp, rest: calls.setdefault("role", role) and 0,
    )

    assert getattr(launch, entry_point)([]) == 0
    assert calls["role"] == role


def test_a_client_role_ensures_a_server_and_releases_it(monkeypatch):
    events = []
    sentinel = object()

    monkeypatch.setattr(
        launch,
        "_ensure_server",
        lambda cp, dp, role: events.append(("ensure", role)) or sentinel,
    )
    monkeypatch.setattr(
        launch, "_release_server", lambda child, cp: events.append(("release", child))
    )
    monkeypatch.setitem(
        sys.modules,
        "bioview_client.configurator",
        types.SimpleNamespace(run_configurator=lambda rest: 0),
    )

    assert launch.run_client("configurator", 9001, 9002, []) == 0
    assert events == [("ensure", "configurator"), ("release", sentinel)]


def test_only_client_roles_and_the_child_server_are_offered():
    """The launcher orchestrates windows; it is not a menu of server options."""
    assert launch.CLIENT_ROLES == ("monitor", "configurator")


def test_the_spawned_server_is_told_to_retire_when_idle(monkeypatch):
    """A shared server must clean itself up: the window that spawned it may not
    be the last one to close."""
    recorded = {}

    def fake_popen(cmd, **kwargs):
        recorded["cmd"] = cmd
        return object()

    monkeypatch.setattr(launch.subprocess, "Popen", fake_popen)
    launch._spawn_server(9001, 9002)

    cmd = recorded["cmd"]
    assert "--exit-when-idle" in cmd
    assert float(cmd[cmd.index("--exit-when-idle") + 1]) > 0


def test_a_server_still_in_use_is_left_alone_when_one_window_closes(monkeypatch):
    terminated = []
    monkeypatch.setattr(
        launch, "_terminate", lambda child, **kw: terminated.append(child)
    )

    class FakeChild:
        def poll(self):
            return None

    child = FakeChild()

    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 1})
    launch._release_server(child, CONTROL_PORT)
    assert terminated == [], "another window is still connected"

    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 0})
    launch._release_server(child, CONTROL_PORT)
    assert terminated == [child], "last window out shuts the server down"

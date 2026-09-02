"""Only the last BioView window standing may shut the shared server down.

Connection counts alone cannot decide that. A window that is still starting up
has no session on the server yet, and the probe that asks for the count can fail
outright -- in which case the old code fell through to killing the server, and
a Configurator opened alongside a Monitor stopped working the moment the
Monitor was closed. Each window registers itself in a small file instead.
"""
import os

import pytest
from bioview_common import CONTROL_PORT

from bioview_client import launch


PORT = 9999


class FakeChild:
    """A spawned server that is still running."""

    def __init__(self):
        self.poll_result = None

    def poll(self):
        return self.poll_result


@pytest.fixture
def terminated(monkeypatch):
    killed = []
    monkeypatch.setattr(launch, "_terminate", lambda child, **kw: killed.append(child))
    return killed


def _other_window(port=PORT, pid=None):
    """Add a registry entry for a different, live process."""
    entries = launch._read_registry()
    # os.getppid() is a real, live pid that is not this process.
    entries.append({"pid": pid or os.getppid(), "role": "monitor", "port": port})
    launch._write_registry(entries)


def test_a_window_registers_and_deregisters_itself():
    launch._register_window("monitor", PORT)
    assert any(e["pid"] == os.getpid() for e in launch._read_registry())

    launch._unregister_window()
    assert not any(e["pid"] == os.getpid() for e in launch._read_registry())


def test_reusing_a_server_still_counts_as_a_window(monkeypatch):
    """The window that started the server is not necessarily the last to close,
    so a window that merely reuses one must be registered just the same."""
    monkeypatch.setattr(launch, "_server_running", lambda **kw: True)

    assert launch._ensure_server(PORT, PORT + 1, role="configurator") is None
    entries = launch._read_registry()
    assert [e["role"] for e in entries] == ["configurator"]


def test_another_open_window_keeps_the_server_alive(terminated, monkeypatch):
    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 0})
    _other_window()

    launch._release_server(FakeChild(), PORT)
    assert terminated == [], "a second window is still open"


def test_a_window_on_a_different_port_is_not_counted(terminated, monkeypatch):
    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 0})
    _other_window(port=PORT + 50)

    child = FakeChild()
    launch._release_server(child, PORT)
    assert terminated == [child], "that window uses a different server"


def test_a_dead_window_does_not_keep_the_server_alive(terminated, monkeypatch):
    """A window that crashed leaves its entry behind; it must be pruned, not
    treated as a live claim on the server forever."""
    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 0})
    launch._write_registry([{"pid": 0x7FFFFFFF, "role": "monitor", "port": PORT}])

    child = FakeChild()
    launch._release_server(child, PORT)
    assert terminated == [child]


def test_the_last_window_out_shuts_the_server_down(terminated, monkeypatch):
    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 0})
    child = FakeChild()
    launch._release_server(child, PORT)
    assert terminated == [child]


def test_a_failed_probe_never_kills_the_server(terminated, monkeypatch):
    """Not knowing is not a licence to kill: the server was spawned with
    --exit-when-idle and retires on its own if it really has been abandoned."""
    monkeypatch.setattr(launch, "_server_info", lambda **kw: None)

    launch._release_server(FakeChild(), PORT)
    assert terminated == []


def test_a_connected_client_keeps_the_server_alive(terminated, monkeypatch):
    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 1})

    launch._release_server(FakeChild(), CONTROL_PORT)
    assert terminated == []


def test_releasing_removes_this_window_from_the_registry(monkeypatch):
    monkeypatch.setattr(launch, "_server_info", lambda **kw: {"clients": 1})
    launch._register_window("monitor", PORT)

    launch._release_server(None, PORT)
    assert launch._read_registry() == []


def test_a_corrupt_registry_is_treated_as_empty(isolated_window_registry):
    isolated_window_registry.write_text("not json at all", encoding="utf-8")
    assert launch._read_registry() == []


def test_pid_liveness_recognizes_this_process():
    assert launch._pid_alive(os.getpid())
    assert not launch._pid_alive(0x7FFFFFFF)
    assert not launch._pid_alive(0)

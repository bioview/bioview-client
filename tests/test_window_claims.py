"""Only the last BioView window standing may shut the shared server down.

Connection counts alone cannot decide that: a window that is still starting up
has no session on the server yet, so a Configurator opened alongside a Monitor
used to stop working the moment the Monitor was closed.

This was a file of pids for a while -- every window registering itself, pruned
by asking the OS which pids were still alive. The window now tells the *server*
instead, with an opaque token carried on the heartbeat, because the server is
what needs to know and it is the only party that cannot be wrong about it. No
file, no lock, no pid liveness, and nothing to be confused by a reused pid.
"""
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


@pytest.fixture
def probes(monkeypatch):
    """Record what each probe told the server, and reply with a fixed count."""
    seen = []

    def _install(reply):
        def _probe(**kwargs):
            seen.append(kwargs)
            return reply

        monkeypatch.setattr(launch, "_server_info", _probe)
        return seen

    return _install


def test_a_window_identifies_itself_the_same_way_every_time():
    """The token is what the server counts, so it has to be stable for the life
    of the process -- and different from any other window's."""
    first = launch._claim_params()
    second = launch._claim_params()
    assert first["window"] == second["window"]
    assert first["window"]


def test_a_claim_says_how_often_it_will_be_renewed():
    """The server sizes the claim's lifetime from this, so changing the
    heartbeat interval cannot silently outrun a lifetime fixed server-side."""
    assert launch._claim_params()["heartbeat"] == launch.SERVER_HEARTBEAT_S


def test_leaving_is_the_same_exchange_as_asking(probes, terminated):
    """The withdrawal and the question are one round trip: the server drops
    this window's claim and answers with what is left."""
    seen = probes({"clients": 0, "windows": 0})

    launch._release_server(FakeChild(), PORT)
    assert [p.get("leaving") for p in seen] == [True]


def test_another_open_window_keeps_the_server_alive(probes, terminated):
    probes({"clients": 0, "windows": 1})

    launch._release_server(FakeChild(), PORT)
    assert terminated == [], "a second window is still open"


def test_a_connected_client_keeps_the_server_alive(probes, terminated):
    probes({"clients": 1, "windows": 0})

    launch._release_server(FakeChild(), CONTROL_PORT)
    assert terminated == []


def test_the_last_window_out_shuts_the_server_down(probes, terminated):
    probes({"clients": 0, "windows": 0})

    child = FakeChild()
    launch._release_server(child, PORT)
    assert terminated == [child]


def test_a_failed_probe_never_kills_the_server(probes, terminated):
    """Not knowing is not a licence to kill: the server was spawned with
    --exit-when-idle and retires on its own if it really has been abandoned."""
    probes(None)

    launch._release_server(FakeChild(), PORT)
    assert terminated == []


def test_a_server_this_window_did_not_spawn_is_never_killed(probes, terminated):
    """Reusing a server does not confer the right to shut it down."""
    probes({"clients": 0, "windows": 0})

    launch._release_server(None, PORT)
    assert terminated == []

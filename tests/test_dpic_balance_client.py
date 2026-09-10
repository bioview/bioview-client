"""Balance must not run on the GUI thread, and must be followed by polling.

`SettingsPanel.run_dpic_balance` is connected to `Client.run_dpic_balance`, and
`Client` is a `QThread` -- the object lives in the GUI thread, so an auto
connection calls the slot *there*. Doing the minute-long round trip in that
call froze the window; the command also carried no timeout override, so it gave
up after the socket's timeout and left the server's real reply to be read as
the answer to the next command.
"""

import json

import pytest
from bioview_common import Command, Response

from bioview_client.handler import Client


def _framed(response: Response, params: dict) -> bytes:
    return json.dumps({"type": response.name, "payload": params}).encode("utf-8")


class _FakeServer:
    """Answers the balance command, then reports progress on status polls."""

    def __init__(
        self,
        polls_pending=2,
        ok=True,
        message="DPIC balance complete",
        running=("USRP",),
    ):
        self.polls_pending = polls_pending
        self.ok = ok
        self.message = message
        self.running = list(running)
        self.sent = []

    def __call__(self, command, params=None, timeout=None):
        self.sent.append((command, timeout))
        if command == Command.RUN_DPIC_BALANCE:
            return _framed(Response.SUCCESS, {"pending": True, "message": "started"})
        if self.polls_pending > 0:
            self.polls_pending -= 1
            return _framed(
                Response.SUCCESS,
                {"dpic_balances": {d: {"pending": True} for d in self.running}},
            )
        return _framed(
            Response.SUCCESS,
            {
                "dpic_balances": {
                    d: {
                        "pending": False,
                        "ok": self.ok,
                        "message": self.message,
                        "results": [],
                        "device_id": d,
                    }
                    for d in self.running
                }
            },
        )


@pytest.fixture
def client(qapp, monkeypatch):
    c = Client()
    # Polls are otherwise a second apart; the loop under test is the same.
    monkeypatch.setattr("bioview_client.handler.DPIC_BALANCE_POLL_INTERVAL", 0.0)
    return c


def test_poll_loop_returns_the_servers_outcome(client, monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(client, "_send_command_locked", server)

    ok, message = client._run_dpic_balance_blocking("USRP")

    assert ok is True
    assert message == "DPIC balance complete"
    # The command itself is bounded by the device-operation timeout, never left
    # on whatever the socket happened to carry.
    assert server.sent[0][0] == Command.RUN_DPIC_BALANCE
    assert server.sent[0][1] is not None
    # ...and the long wait is the poll loop, not that one read.
    assert [c for c, _ in server.sent].count(Command.GET_DEVICE_STATUS) == 3


def test_a_failed_balance_is_reported_as_one(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "_send_command_locked",
        _FakeServer(polls_pending=0, ok=False, message="no metric was readable"),
    )

    ok, message = client._run_dpic_balance_blocking("USRP")

    assert ok is False
    assert message == "no metric was readable"


def test_a_refused_start_is_not_polled(client, monkeypatch):
    def _refuse(command, params=None, timeout=None):
        return _framed(Response.ERROR, {"message": "Device handler not found"})

    monkeypatch.setattr(client, "_send_command_locked", _refuse)

    ok, message = client._run_dpic_balance_blocking("USRP")

    assert ok is False
    assert message == "Device handler not found"


def test_run_dpic_balance_returns_immediately(qapp, client, monkeypatch):
    """The GUI thread dispatches and returns; it never waits for the search."""
    started, finished = [], []
    client.dpic_balance_started.connect(started.append)
    client.dpic_balance_finished.connect(lambda dev, ok, msg: finished.append(ok))

    monkeypatch.setattr(client, "_send_command_locked", _FakeServer())

    assert client.run_dpic_balance("USRP") is True
    assert started == ["USRP"]

    # Balances have their own pool: on the shared one, a rig with several
    # groups balancing at once would leave no worker for Start or Stop.
    assert client.balance_pool.waitForDone(10_000)
    # The worker's finished signal is delivered on the event loop.
    for _ in range(50):
        qapp.processEvents()
        if finished:
            break
    assert finished == [True]
    assert client._dpic_balance_running == set()


def test_a_second_balance_is_refused_while_one_runs(client, monkeypatch):
    monkeypatch.setattr(client, "_send_command_locked", _FakeServer(polls_pending=50))

    assert client.run_dpic_balance("USRP") is True
    assert client.run_dpic_balance("USRP") is False


def test_another_group_balances_while_the_first_one_runs(client, monkeypatch):
    """Groups are separate hardware; only the same group's search blocks one."""
    monkeypatch.setattr(
        client,
        "_send_command_locked",
        _FakeServer(polls_pending=50, running=("USRP1", "USRP2")),
    )

    assert client.run_dpic_balance("USRP1") is True
    assert client.run_dpic_balance("USRP2") is True
    assert client._dpic_balance_running == {"USRP1", "USRP2"}


def test_a_poll_reads_this_groups_own_outcome(client, monkeypatch):
    """One group finishing must not end another group's wait.

    Both states arrive in the same status reply. Reading the singular
    ``dpic_balance`` field -- whichever group started last -- made the first
    group report the second one's result and stop polling its own.
    """
    payload = {
        "dpic_balances": {
            "USRP1": {"pending": True},
            "USRP2": {"pending": False, "ok": True, "message": "USRP2 done"},
        }
    }

    assert client._dpic_state_for(payload, "USRP1") == {"pending": True}
    assert client._dpic_state_for(payload, "USRP2")["message"] == "USRP2 done"
    # A group the server has not recorded yet is waited for, not resolved.
    assert client._dpic_state_for(payload, "USRP3") == {}


def test_a_server_without_the_per_group_map_is_still_followed(client):
    """Old servers report one balance; it counts only for the group it names."""
    legacy = {"dpic_balance": {"pending": False, "ok": True, "device_id": "USRP1"}}

    assert client._dpic_state_for(legacy, "USRP1")["ok"] is True
    assert client._dpic_state_for(legacy, "USRP2") == {}
    # No balance state at all cannot be polled and must not be waited on.
    assert client._dpic_state_for({}, "USRP1") is None

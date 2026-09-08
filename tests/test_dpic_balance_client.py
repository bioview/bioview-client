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

    def __init__(self, polls_pending=2, ok=True, message="DPIC balance complete"):
        self.polls_pending = polls_pending
        self.ok = ok
        self.message = message
        self.sent = []

    def __call__(self, command, params=None, timeout=None):
        self.sent.append((command, timeout))
        if command == Command.RUN_DPIC_BALANCE:
            return _framed(Response.SUCCESS, {"pending": True, "message": "started"})
        if self.polls_pending > 0:
            self.polls_pending -= 1
            return _framed(Response.SUCCESS, {"dpic_balance": {"pending": True}})
        return _framed(
            Response.SUCCESS,
            {
                "dpic_balance": {
                    "pending": False,
                    "ok": self.ok,
                    "message": self.message,
                    "results": [],
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

    assert client.thread_pool.waitForDone(10_000)
    # The worker's finished signal is delivered on the event loop.
    for _ in range(50):
        qapp.processEvents()
        if finished:
            break
    assert finished == [True]
    assert client._dpic_balance_running is False


def test_a_second_balance_is_refused_while_one_runs(client, monkeypatch):
    monkeypatch.setattr(client, "_send_command_locked", _FakeServer(polls_pending=50))

    assert client.run_dpic_balance("USRP") is True
    assert client.run_dpic_balance("USRP") is False

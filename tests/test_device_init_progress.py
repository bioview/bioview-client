"""Device groups reach the status bar as they come up, not all at the end.

The server brings the groups up one at a time and has always reported each one
as it lands. The client kept that to a debug log line, so a rig whose third
radio was hanging looked exactly like one that was merely slow: every indicator
sat on "connecting" until the whole command finished.
"""

import pytest
from bioview_common import Command, DeviceStatus, Response

from bioview_client.handler import Client
from bioview_client.workers import DeviceInitWorker


@pytest.fixture
def client(qapp):
    return Client()


def _progress(client):
    seen = []
    client.device_init_progress.connect(
        lambda status, errors: seen.append((dict(status), dict(errors)))
    )
    return seen


def test_a_mid_command_status_is_published(client):
    seen = _progress(client)
    client._on_device_command_progress(
        {"RF": DeviceStatus.CONNECTED.value, "MIC": DeviceStatus.CONNECTING.value}, {}
    )

    status, _errors = seen[-1]
    assert status["RF"] == DeviceStatus.CONNECTED.value
    assert status["MIC"] == DeviceStatus.CONNECTING.value


def test_progress_carries_the_reason_a_group_failed(client):
    seen = _progress(client)
    client._on_device_command_progress(
        {"RF": DeviceStatus.UNAVAILABLE.value}, {"RF": "no USRP hardware was found"}
    )

    _status, errors = seen[-1]
    assert errors["RF"] == "no USRP hardware was found"
    # Kept on the client too, so the completion report can explain the group
    # without asking the server again.
    assert client.device_errors["RF"] == "no USRP hardware was found"


def test_progress_does_not_decide_the_client_status(client):
    """Only the completion handler moves the client's state machine."""
    before = client.status
    client._on_device_command_progress({"RF": DeviceStatus.CONNECTED.value}, {})
    assert client.status == before


class _FakeClient:
    """Enough of a Client for the worker's poll loop."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.logged = []

        class _Signal:
            def __init__(self, sink):
                self.sink = sink

            def emit(self, *args):
                self.sink.append(args)

        self.log_message = _Signal(self.logged)
        self.device_errors = {}
        self.data_sources = None

    def _send_command_locked(self, command=None, timeout=None, params=None):
        return self.replies.pop(0) if self.replies else None


def _reply(pending, status, errors=None):
    return {
        "type": Response.SUCCESS.name,
        "params": {
            "pending": pending,
            "device_status": status,
            "device_errors": errors or {},
        },
    }


def test_the_poll_publishes_each_change_it_sees(qapp, monkeypatch):
    """Two groups landing one after another are two separate reports."""
    import bioview_client.workers as workers

    monkeypatch.setattr(workers, "DEVICE_OP_POLL_INTERVAL", 0)
    monkeypatch.setattr(
        workers,
        "parse_and_validate_response",
        lambda r: (r["type"], r["params"]),
    )

    fake = _FakeClient(
        [
            _reply(True, {"RF": "Connecting", "MIC": "Connecting"}),
            _reply(True, {"RF": "Connected", "MIC": "Connecting"}),
            _reply(False, {"RF": "Connected", "MIC": "Connected"}),
        ]
    )
    worker = DeviceInitWorker.__new__(DeviceInitWorker)
    worker.client_ref = fake
    worker.command = Command.INITIALIZE_DEVICES
    worker.signals = workers.DeviceInitSignals()

    seen = []
    worker.signals.progress.connect(lambda s, e: seen.append(dict(s)))

    import time

    worker._poll_until_complete(time.monotonic() + 5)

    assert seen[0] == {"RF": "Connecting", "MIC": "Connecting"}
    assert seen[-1] == {"RF": "Connected", "MIC": "Connecting"}


def test_an_empty_status_map_is_not_published(qapp):
    """The server clears its group states while it re-runs discovery."""
    import bioview_client.workers as workers

    worker = DeviceInitWorker.__new__(DeviceInitWorker)
    worker.signals = workers.DeviceInitSignals()

    seen = []
    worker.signals.progress.connect(lambda s, e: seen.append(s))
    worker._emit_progress({}, {})

    assert seen == []

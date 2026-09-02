"""Latching onto a localhost server, and staying latched where that is wanted.

The Configurator can only ever talk to the server on this machine, so it should
reconnect on its own if that server is restarted -- a timer that stopped at the
first successful connect left the window dead for the rest of its life. The
Monitor has a server picker, so for it the timer still stops: otherwise a user
who disconnected on purpose would be dragged back to localhost a second later.
"""
from bioview_common import ClientStatus

from bioview_client.autoconnect import start_localhost_autoconnect


class FakeClient:
    def __init__(self, status=ClientStatus.SERVER_DISCONNECTED):
        self.status = status
        self.attempts = 0

    def quick_connect_localhost(self):
        self.attempts += 1
        self.status = ClientStatus.SERVER_CONNECTED


def _tick(timer):
    """Fire the timer's slot without waiting on the Qt event loop."""
    timer.timeout.emit()


def test_it_connects_immediately_rather_than_waiting_a_whole_interval(qapp):
    client = FakeClient()
    timer = start_localhost_autoconnect(client)
    assert client.attempts == 1
    timer.stop()


def test_the_monitor_stops_probing_once_connected(qapp):
    client = FakeClient()
    timer = start_localhost_autoconnect(client)

    _tick(timer)
    assert not timer.isActive(), "connected, and free to pick another server"
    assert client.attempts == 1


def test_a_latched_window_keeps_its_timer_running(qapp):
    client = FakeClient()
    timer = start_localhost_autoconnect(client, keep_latched=True)

    _tick(timer)
    assert timer.isActive()
    assert client.attempts == 1, "no reconnect attempt while the connection holds"
    timer.stop()


def test_a_latched_window_reconnects_after_its_server_restarts(qapp):
    client = FakeClient()
    timer = start_localhost_autoconnect(client, keep_latched=True)

    client.status = ClientStatus.SERVER_DISCONNECTED
    _tick(timer)

    assert client.attempts == 2
    assert client.status == ClientStatus.SERVER_CONNECTED
    timer.stop()


def test_no_client_means_no_timer():
    assert start_localhost_autoconnect(None) is None

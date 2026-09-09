"""A window whose server dies has to be able to say so, and to recover.

Losing the server used to be terminal for a window: the handler noticed and
dropped the connected state, but both retry timers had already stopped
themselves on the first successful connect and nothing ever started them again.
The window sat disconnected forever, even once a server was back and answering
on the very port it had been watching. Nothing said what had happened either --
the only sign was a status bar going red and every device command failing.
"""
import pytest
from bioview_common import ClientStatus, DeviceStatus
from PyQt6.QtWidgets import QDialog

from bioview_client.components import ServerLostDialog
from bioview_client.handler import Client


@pytest.fixture
def client(qapp):
    return Client()


class FakeTimer:
    """A stand-in for the retry QTimers the window stashes on itself."""

    def __init__(self, active=False):
        self.active = active
        self.starts = 0

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.starts += 1


class FakeWindow:
    """Just enough of BioViewMonitor to exercise the recovery helpers."""

    def __init__(self, **timers):
        from bioview_client.monitor import BioViewMonitor

        self.device_status = {"grp": DeviceStatus.CONNECTED}
        self.status_bar = type(
            "_Bar", (), {"update_device_status": lambda self, *a: None}
        )()
        for name, timer in timers.items():
            setattr(self, name, timer)

        self._forget = BioViewMonitor._forget_device_status.__get__(self)
        self._resume = BioViewMonitor._resume_reconnect_attempts.__get__(self)


def test_a_lost_server_is_told_apart_from_a_disconnect_the_user_asked_for(client):
    """Only one of the two is a fault, and only one may pull the window back
    onto a server it was deliberately taken off."""
    seen = []
    client.server_lost.connect(seen.append)

    client.status = ClientStatus.SERVER_CONNECTED
    client.selected_server = {"ip": "127.0.0.1"}
    client._handle_lost_server()
    assert [s["ip"] for s in seen] == ["127.0.0.1"]

    client.status = ClientStatus.SERVER_CONNECTED
    client.disconnect_from_server()
    assert len(seen) == 1, "the user's own Disconnect is not a lost server"


def test_a_server_lost_while_already_disconnected_is_not_reported_twice(client):
    seen = []
    client.server_lost.connect(seen.append)

    client.status = ClientStatus.SERVER_DISCONNECTED
    client._handle_lost_server()
    assert seen == []


def test_losing_the_local_server_resumes_both_retry_timers(qapp):
    localhost, rescan = FakeTimer(), FakeTimer()
    window = FakeWindow(_localhost_timer=localhost, _rescan_timer=rescan)

    window._resume(was_local=True)
    assert localhost.starts == 1
    assert rescan.starts == 1


def test_losing_a_lan_server_does_not_latch_the_window_onto_localhost(qapp):
    """Resuming the localhost probe would quietly move the window to a server
    on a different machine than the one the user chose."""
    localhost, rescan = FakeTimer(), FakeTimer()
    window = FakeWindow(_localhost_timer=localhost, _rescan_timer=rescan)

    window._resume(was_local=False)
    assert localhost.starts == 0
    assert rescan.starts == 1


def test_a_timer_that_is_already_running_is_left_alone(qapp):
    localhost = FakeTimer(active=True)
    window = FakeWindow(_localhost_timer=localhost, _rescan_timer=FakeTimer())

    window._resume(was_local=True)
    assert localhost.starts == 0


def test_a_window_built_without_timers_does_not_fall_over(qapp):
    """The timers are attached by run_monitor, so a directly built window has
    none; recovery must degrade rather than raise."""
    FakeWindow()._resume(was_local=True)


def test_device_status_is_forgotten_when_the_server_goes(qapp):
    """Whatever was initialized belonged to a server this window can no longer
    reach; leaving it on screen shows ready devices that are not there."""
    window = FakeWindow()
    window._forget()
    assert window.device_status == {"grp": DeviceStatus.NOINIT}


def test_the_dialog_closes_itself_when_the_restart_works(qapp):
    dialog = ServerLostDialog(restart=lambda: None)
    dialog._on_restart_succeeded()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_a_failed_restart_explains_itself_and_offers_a_way_out(qapp):
    """The dialog is the only thing the user is looking at, so the reason has
    to land here rather than in a log window that may not even be open."""
    dialog = ServerLostDialog(restart=lambda: None)
    assert dialog.quit_btn.isHidden(), "quitting is a last resort, not an opener"

    dialog._on_restart_failed("port 8998 is held by something else")

    assert "port 8998" in dialog.error.text()
    assert not dialog.quit_btn.isHidden()
    assert dialog.restart_btn.isEnabled(), "the user may try again"
    assert dialog.cancel_btn.isEnabled(), "and may still carry on without one"


def test_quitting_is_reported_distinctly_from_dismissing(qapp):
    """Continuing without a server and closing the app are different answers."""
    dialog = ServerLostDialog(restart=lambda: None)
    dialog._on_quit()
    assert dialog.result() == ServerLostDialog.QUIT
    assert QDialog.DialogCode.Accepted != ServerLostDialog.QUIT
    assert QDialog.DialogCode.Rejected != ServerLostDialog.QUIT


def test_a_restart_in_progress_cannot_be_double_started(qapp):
    dialog = ServerLostDialog(restart=lambda: None)
    dialog._set_busy(True)
    assert not dialog.restart_btn.isEnabled()
    assert not dialog.cancel_btn.isEnabled()


def test_the_local_server_is_the_only_one_a_window_offers_to_restart():
    from bioview_client.monitor import _is_local_server

    assert _is_local_server({"ip": "127.0.0.1"})
    assert _is_local_server({"ip": "localhost"})
    assert not _is_local_server({"ip": "192.168.1.40"})
    assert not _is_local_server({})

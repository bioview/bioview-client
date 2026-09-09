"""The bar keeps saying something useful while an operation drags on.

A message that never changes is indistinguishable from a hang, and a device
group that comes up on its own has to reach the bar when it comes up rather
than when the last group in the rig has finished.
"""

import pytest
from bioview_common import ClientStatus, DeviceStatus
from PyQt6.QtWidgets import QMainWindow

from bioview_client.components.status_bar import StatusBar


@pytest.fixture
def status_bar(qapp):
    window = QMainWindow()
    bar = StatusBar(device_status={"BIOPAC": DeviceStatus.NOINIT}, parent=window)
    window.setStatusBar(bar)
    # The window owns the bar in C++; without a Python reference it is
    # collected at the end of the fixture and takes the bar's widgets with it.
    bar._test_window = window
    yield bar
    window.close()


def test_a_slow_operation_escalates_its_message(status_bar, qapp):
    status_bar.show_activity(
        "Connecting to lab-pc…",
        level="info",
        slow_message="Still connecting to lab-pc",
        slow_after_ms=10,
    )
    assert status_bar.activity_label.text() == "Connecting to lab-pc…"
    assert status_bar._slow_timer.isActive()

    status_bar._on_activity_slow()
    assert status_bar.activity_label.text() == "Still connecting to lab-pc"


def test_the_escalation_only_fires_once(status_bar):
    status_bar.show_activity("Working…", slow_message="Still working", slow_after_ms=10)
    status_bar._on_activity_slow()
    status_bar.activity_label.setText("something else")
    status_bar._on_activity_slow()

    assert status_bar.activity_label.text() == "something else"


def test_a_finished_operation_cancels_its_own_escalation(status_bar):
    """The operation ended, so "still connecting" must never appear after it."""
    status_bar.show_activity(
        "Connecting…", slow_message="Still connecting", slow_after_ms=10_000
    )
    status_bar.show_activity("Connected", level="success")

    assert not status_bar._slow_timer.isActive()
    assert status_bar._slow_message is None


def test_clearing_the_bar_cancels_the_escalation(status_bar):
    status_bar.show_activity(
        "Connecting…", slow_message="Still connecting", slow_after_ms=10_000
    )
    status_bar.clear_activity()

    assert not status_bar._slow_timer.isActive()
    assert status_bar.activity_label.text() == ""


def test_a_message_without_an_escalation_arms_no_timer(status_bar):
    status_bar.show_activity("Streaming started", level="streaming")
    assert not status_bar._slow_timer.isActive()


def test_the_bar_says_which_server_it_is_reaching(status_bar):
    status_bar.set_server_connecting("lab-pc")

    assert "lab-pc" in status_bar.server_status_label.text()
    assert status_bar.server_indicator.status == DeviceStatus.CONNECTING

    status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)
    assert status_bar.server_indicator.status == DeviceStatus.CONNECTED


def test_connecting_without_a_name_still_reads_as_connecting(status_bar):
    status_bar.set_server_connecting("")
    assert "Connecting" in status_bar.server_status_label.text()


def test_a_group_the_bar_was_not_built_with_is_added_on_first_report(status_bar):
    """A configuration loaded after the window opened must still be shown."""
    assert "USRP_A" not in status_bar.device_status_panel.device_widgets

    status_bar.update_device_status("USRP_A", DeviceStatus.CONNECTING)

    widget = status_bar.device_status_panel.device_widgets["USRP_A"]
    assert widget.device_status == DeviceStatus.CONNECTING

    status_bar.update_device_status("USRP_A", DeviceStatus.CONNECTED)
    assert widget.device_status == DeviceStatus.CONNECTED

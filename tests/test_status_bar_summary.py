"""The status bar states one server fact; the picker lives in a flyout.

A scan button, a server dropdown and three more buttons used to sit in the bar
permanently, for a decision made once a session. The bar now says whether the
server is there and hands the controls to a bottom sheet behind "More...".
"""

import pytest
from bioview_common import ClientStatus, DeviceStatus

from bioview_client.components.status_bar import ServerConnector, StatusBar


@pytest.fixture
def status_bar(qapp):
    from PyQt6.QtWidgets import QMainWindow

    window = QMainWindow()
    bar = StatusBar(device_status={"BIOPAC": DeviceStatus.NOINIT}, parent=window)
    window.setStatusBar(bar)
    window.resize(1200, 800)
    # The window owns the bar in C++; without a Python reference it is
    # collected at the end of the fixture and takes the bar's widgets with it.
    bar._test_window = window
    yield bar
    window.close()


def test_the_bar_states_only_the_overall_server_status(status_bar):
    status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)
    assert status_bar.server_status_label.text() == "BioView Server Connected"

    status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
    assert status_bar.server_status_label.text() == "BioView Server Disconnected"


def test_a_scan_in_flight_is_not_reported_as_disconnected(status_bar):
    status_bar.set_server_status(ClientStatus.SCANNING)
    assert "Searching" in status_bar.server_status_label.text()
    # Blinking, the same way a connecting device does.
    assert status_bar.server_indicator.status == DeviceStatus.CONNECTING


def test_the_picker_moved_into_the_flyout(status_bar):
    connector = status_bar.server_connector
    assert isinstance(connector, ServerConnector)
    # The bar itself no longer carries any of it...
    assert connector.parentWidget() is not status_bar.container
    # ...and the flyout holds the bar's own connector, not a second copy, so
    # the scan state and every signal the monitor wired stay put.
    assert status_bar.server_flyout.connector is connector


def test_the_flyout_opens_against_the_bottom_of_the_window(status_bar, qapp):
    window = status_bar.window()
    window.show()
    qapp.processEvents()

    status_bar.open_server_flyout()
    qapp.processEvents()

    flyout = status_bar.server_flyout
    assert flyout.isVisible()
    assert flyout.isModal()

    bottom = window.mapToGlobal(window.rect().bottomLeft())
    assert flyout.geometry().bottom() <= bottom.y()
    assert flyout.width() <= window.width()

    flyout.reject()


def test_connecting_still_drives_the_flyout_buttons(status_bar):
    status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)
    connector = status_bar.server_connector
    assert connector.connect_btn.isEnabled() is False
    assert connector.disconnect_btn.isEnabled() is True
    assert connector.discover_btn.isEnabled() is True

    status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
    assert connector.disconnect_btn.isEnabled() is False
    assert connector.discover_btn.isEnabled() is False

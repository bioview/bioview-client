"""Feedback while a device configuration change is being applied.

Pressing Save used to close the editor immediately and fire the request off,
which left the log panel as the only sign that anything was happening -- and no
sign at all of whether it worked. The dialog now stays up, shows a busy
indicator, and reports Completed or Failed before it closes.
"""
import time

import pytest
from PyQt6.QtWidgets import QApplication

from bioview_client.configurator import DeviceConfigDialog


DEVICE = {
    "device_type": "usrp",
    "name": "MyB210",
    "serial": "31ABCDE",
    "eeprom_name": "MyB210",
}
PROPERTIES = {
    "device_name": {
        "type": "text",
        "display_name": "Device Name",
        "required": True,
        "max_length": 64,
    }
}


@pytest.fixture
def dialog(qapp):
    dlg = DeviceConfigDialog(dict(DEVICE), dict(PROPERTIES))
    yield dlg
    dlg.deleteLater()


def _save(dialog, name):
    dialog.property_widgets["device_name"].setText(name)
    dialog.save_btn.click()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_saving_hands_the_values_up_without_closing(dialog):
    seen = []
    dialog.save_requested.connect(lambda info, cfg: seen.append((info, cfg)))

    _save(dialog, "Radio A")

    assert seen == [(DEVICE, {"device_name": "Radio A"})]
    assert dialog.result() == 0, "the dialog must stay open until the server answers"
    assert dialog.progress.isVisible() or dialog._updating
    assert not dialog.save_btn.isEnabled()
    assert not dialog.cancel_btn.isEnabled()
    assert not dialog.property_widgets["device_name"].isEnabled()
    assert "Updating" in dialog.status_label.text()


def test_an_update_in_flight_cannot_be_cancelled(dialog):
    _save(dialog, "Radio A")
    dialog.reject()
    assert dialog.result() == 0


def test_a_successful_update_reports_completion_and_closes(dialog):
    closed = []
    dialog.accepted.connect(lambda: closed.append(True))

    _save(dialog, "Radio A")
    dialog.update_finished(True, "Renamed to Radio A.")

    assert dialog.status_label.text() == "Completed"
    assert _wait_for(lambda: closed), "the dialog should close itself once done"


def test_a_failed_update_says_so_and_stays_open_for_another_try(dialog):
    _save(dialog, "Radio A")
    dialog.update_finished(False, "Another device already uses that name.")

    assert dialog.status_label.text() == "Failed"
    assert not dialog.progress.isVisible()
    assert dialog.error_label.isVisible() or "already uses" in dialog.error_label.text()
    assert dialog.save_btn.isEnabled(), "the user has to be able to correct the name"
    assert dialog.cancel_btn.isEnabled()
    assert dialog.property_widgets["device_name"].isEnabled()
    assert dialog.result() == 0


def test_an_invalid_value_never_reaches_the_server(dialog):
    seen = []
    dialog.save_requested.connect(lambda info, cfg: seen.append(cfg))

    _save(dialog, "   ")

    assert seen == []
    assert "empty" in dialog.error_label.text().lower()
    assert dialog.save_btn.isEnabled()

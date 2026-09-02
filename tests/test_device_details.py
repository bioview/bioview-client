"""What the Configurator shows for a discovered device.

Every device used to render as "<type>  ·  S/N Unknown" unless it happened to
report a serial number, which told the user nothing about a BIOPAC unit (an
MP36 supplies no USB serial at all).
"""
import pytest

from bioview_client.components import device_details as details
from bioview_client.components import device_is_healthy


def test_a_device_without_a_serial_still_identifies_itself():
    line = details(
        {
            "device_type": "biopac",
            "name": "BIOPAC MP36 USB Data Acquisition Unit",
            "model": "MP36",
            "serial": None,
            "manufacturer": "BIOPAC Systems, Inc.",
            "status": "OK",
        }
    )
    assert "MP36" in line
    assert "BIOPAC Systems, Inc." in line
    assert "S/N" not in line, "no serial to show, so do not invent one"


def test_a_serial_is_shown_when_the_device_reports_one():
    line = details({"device_type": "usrp", "serial": "31ABCDE"})
    assert "S/N 31ABCDE" in line


def test_an_unusable_device_is_flagged_in_its_details():
    line = details({"device_type": "biopac", "model": "MP36", "status": "Error"})
    assert "Error" in line


def test_a_device_with_nothing_to_report_still_renders():
    assert details({}) == "Unknown"


@pytest.mark.parametrize("status", ["OK", "Available", None, ""])
def test_devices_the_os_is_happy_with_are_healthy(status):
    assert device_is_healthy({"status": status})


@pytest.mark.parametrize("status", ["Error", "Degraded", "Unknown"])
def test_a_device_the_os_cannot_drive_is_not_healthy(status):
    # A driver that fails to load still leaves the device discoverable, so this
    # is the only warning the user gets before acquisition fails to open it.
    assert not device_is_healthy({"status": status})


def test_a_device_with_no_status_at_all_is_assumed_healthy():
    """Backends that cannot report OS state (USRP) must not look broken."""
    assert device_is_healthy({"device_type": "usrp", "serial": "31ABCDE"})

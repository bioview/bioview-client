"""One place decides which server faults a window raises, and how often.

UHD not matching its bindings and a backend whose driver is missing are the
same kind of problem in the Monitor and in the Configurator, so neither window
words them itself. They differ only in which faults concern them.
"""

import pytest

from bioview_client.components.diagnostics import (
    DiagnosticsReporter,
    relevant_diagnostics,
)


ISSUES = [
    {
        "id": "lab-pc:backend:usrp:uhd-not-installed",
        "device_type": "usrp",
        "level": "error",
        "title": "The USRP driver (UHD) is not installed on the server",
        "message": "Install it, then restart BioView.",
        "detail": "No module named 'uhd'",
    },
    {
        "id": "lab-pc:backend:biopac:biopac-driver-not-responding",
        "device_type": "biopac",
        "level": "error",
        "title": "The BIOPAC driver is not responding",
        "message": "Reinstall the driver.",
        "detail": "MPDRVERR",
    },
]


@pytest.fixture
def reporter(qapp, monkeypatch):
    """A reporter whose modal is recorded instead of shown."""
    shown = []
    reporter = DiagnosticsReporter()
    monkeypatch.setattr(reporter, "_show", shown.append)
    reporter.shown = shown
    return reporter


def test_a_window_with_no_filter_sees_every_fault():
    """The Configurator: hardware setup is the only thing it does."""
    assert relevant_diagnostics(ISSUES) == ISSUES


def test_a_window_filters_to_the_device_types_it_uses():
    """The Monitor: a missing BIOPAC driver is not this session's problem."""
    relevant = relevant_diagnostics(ISSUES, {"usrp"})
    assert [i["device_type"] for i in relevant] == ["usrp"]


def test_the_filter_is_case_insensitive():
    assert relevant_diagnostics(ISSUES, {"USRP"})


def test_a_configuration_using_neither_backend_is_left_alone():
    assert relevant_diagnostics(ISSUES, {"microphone"}) == []


def test_each_fault_is_shown_once_however_often_it_is_reported(reporter):
    """Reconnecting three times must not raise the same modal three times."""
    assert len(reporter.report(ISSUES)) == 2
    assert reporter.report(ISSUES) == []
    assert reporter.report(ISSUES) == []
    assert len(reporter.shown) == 1


def test_a_new_fault_is_still_raised_after_an_old_one(reporter):
    reporter.report([ISSUES[0]])
    fresh = reporter.report(ISSUES)

    assert [i["device_type"] for i in fresh] == ["biopac"]
    assert len(reporter.shown) == 2


def test_a_reset_lets_a_fault_be_reported_again(reporter):
    reporter.report(ISSUES)
    reporter.reset()

    assert len(reporter.report(ISSUES)) == 2


def test_nothing_to_report_shows_no_dialog(reporter):
    assert reporter.report([]) == []
    assert reporter.shown == []


def test_filtering_and_deduplication_compose(reporter):
    assert len(reporter.report(ISSUES, {"usrp"})) == 1
    # The BIOPAC fault was filtered out, not marked as seen, so a window that
    # later cares about it still gets it.
    assert len(reporter.report(ISSUES, {"usrp", "biopac"})) == 1

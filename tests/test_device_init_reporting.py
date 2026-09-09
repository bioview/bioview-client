"""A half-initialized rig has to say so, in front of the operator.

Every device outcome was already written to the experiment log, but a session
starts with the log window closed: two radios up and one down looked exactly
like three up until a plot stayed flat. The client now reports which groups
failed and why, as data rather than as log lines, so the monitor can put it in a
modal.
"""

import pytest
from bioview_common import DeviceStatus

from bioview_client.handler import Client


@pytest.fixture
def client(qapp):
    return Client()


def _report(client):
    """The last (failures, only_discover) pair the client emitted."""
    seen = []
    client.device_init_report.connect(lambda f, d: seen.append((dict(f), d)))
    return seen


def test_every_group_up_reports_no_failures(client):
    seen = _report(client)
    client._on_device_command_finished(
        {"RF": DeviceStatus.CONNECTED, "MIC": DeviceStatus.CONNECTED}, False
    )
    assert seen[-1] == ({}, False)


def test_a_partial_failure_names_the_group(client):
    seen = _report(client)
    client.device_errors = {"MIC": "no audio input device was found"}
    client._on_device_command_finished(
        {"RF": DeviceStatus.CONNECTED, "MIC": DeviceStatus.DISCONNECTED}, False
    )

    failures, only_discover = seen[-1]
    assert only_discover is False
    assert set(failures) == {"MIC"}
    assert "audio input" in failures["MIC"]


def test_a_group_that_failed_without_a_reason_still_appears(client):
    """The status is the fallback explanation; an unexplained failure must not
    vanish from the report."""
    seen = _report(client)
    client.device_errors = {}
    client._on_device_command_finished({"RF": DeviceStatus.DISCONNECTED}, False)

    failures, _ = seen[-1]
    assert "RF" in failures
    assert failures["RF"]


def test_the_report_precedes_the_outcome_signal(client):
    """The monitor reads the failures in its success handler, so the report has
    to have arrived by then."""
    order = []
    client.device_init_report.connect(lambda f, d: order.append(("report", dict(f))))
    client.device_init_succeeded.connect(lambda s: order.append(("succeeded", None)))

    client.device_errors = {"MIC": "no audio input device was found"}
    client._on_device_command_finished(
        {"RF": DeviceStatus.CONNECTED, "MIC": DeviceStatus.DISCONNECTED}, False
    )

    assert [name for name, _ in order] == ["report", "succeeded"]
    assert order[0][1]["MIC"]


def test_discovery_is_reported_but_flagged_as_such(client):
    """A device merely absent from a scan is not an initialization failure; the
    monitor uses the flag to keep the modal out of the way."""
    seen = _report(client)
    client._on_device_command_finished({"RF": DeviceStatus.UNAVAILABLE}, True)

    failures, only_discover = seen[-1]
    assert only_discover is True
    assert "RF" in failures


def test_available_counts_as_up_during_discovery(client):
    seen = _report(client)
    client._on_device_command_finished({"RF": DeviceStatus.AVAILABLE}, True)
    assert seen[-1] == ({}, True)

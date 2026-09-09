"""Tests for save-file naming conventions, save-target validation, and the
Mark Events annotation routing on the client handler/panel."""
from bioview_client.handler import Client, _sanitize_label


def test_sanitize_label_makes_safe_names():
    assert _sanitize_label("Text Routine") == "Text_Routine"
    assert _sanitize_label("A/B\\C:D") == "A_B_C_D"
    assert _sanitize_label("  ") == "routine"
    assert _sanitize_label("keep-_ok") == "keep-_ok"


def test_has_valid_save_target(qapp):
    client = Client()
    # No file name / folder set -> invalid.
    assert client.has_valid_save_target() is False

    client.set_save_param("file_name", "session")
    assert client.has_valid_save_target() is False  # folder still missing

    client.set_save_param("save_dir", "/tmp/recordings")
    assert client.has_valid_save_target() is True

    # Whitespace-only values are treated as missing.
    client.set_save_param("file_name", "   ")
    assert client.has_valid_save_target() is False


def test_record_annotation_requires_active_recording(qapp, monkeypatch):
    """Annotations go to the server, which owns the recording and its trailer."""
    from bioview_common import Command, Response

    client = Client()
    # No active recording -> nothing is sent.
    assert client.is_recording() is False
    assert client.record_annotation("hello") is False

    sent = []

    def fake_send(command, params=None, timeout=None):
        sent.append((command, params))
        return b"ignored"

    monkeypatch.setattr(client, "_send_command_locked", fake_send)
    monkeypatch.setattr(
        "bioview_client.handler.parse_and_validate_response",
        lambda _raw: (Response.SUCCESS.name, {}),
    )

    client._recording_active = True
    assert client.is_recording() is True
    assert client.record_annotation("event A") is True
    assert sent == [(Command.MARK_EVENT, {"text": "event A"})]


def test_record_annotation_reports_server_refusal(qapp, monkeypatch):
    from bioview_common import Response

    client = Client()
    client._recording_active = True
    monkeypatch.setattr(client, "_send_command_locked", lambda *a, **k: b"ignored")
    monkeypatch.setattr(
        "bioview_client.handler.parse_and_validate_response",
        lambda _raw: (Response.ERROR.name, {"message": "No recording is active"}),
    )
    assert client.record_annotation("event A") is False


def test_save_target_is_sent_to_the_server(qapp, tmp_path):
    """The server writes the file, so the save target travels with Start.

    The client no longer builds the path itself; it only has to hand over the
    name, folder and (for a timed run) the sanitized routine label.
    """
    client = Client()
    client.set_save_enabled(True)
    client.set_save_param("file_name", "session.bvr")
    client.set_save_param("save_dir", str(tmp_path))

    client.set_save_label(None)
    client._start_saving()
    assert client.is_recording() is True

    client.set_save_label("Text Routine")
    assert _sanitize_label(client.save_label) == "Text_Routine"

    # Saving off, or no valid target, means no recording is claimed.
    client.set_save_enabled(False)
    client._start_saving()
    assert client.is_recording() is False


def test_annotation_panel_emits_signal(qapp):
    from bioview_client.components.annotate_event import AnnotateEventPanel

    panel = AnnotateEventPanel()
    received = []
    panel.annotation_requested.connect(received.append)

    # Empty text does not emit.
    panel.annotation_box.setText("   ")
    panel.record_annotation()
    assert received == []

    # Non-empty text emits the trimmed annotation.
    panel.annotation_box.setText("  marked event  ")
    panel.record_annotation()
    assert received == ["marked event"]

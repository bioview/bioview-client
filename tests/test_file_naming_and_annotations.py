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


def test_record_annotation_requires_active_recording(qapp):
    client = Client()
    # No active recording -> annotation is not recorded.
    assert client.is_recording() is False
    assert client.record_annotation("hello") is False

    # With an active recording, the annotation is forwarded to the saver.
    class FakeSaver:
        def __init__(self):
            self.annotations = []

        def record_annotation(self, text):
            self.annotations.append(text)

    client.data_saver = FakeSaver()
    assert client.is_recording() is True
    assert client.record_annotation("event A") is True
    assert client.data_saver.annotations == ["event A"]


def test_timed_mode_filename_labeling(qapp, tmp_path):
    """A timed-mode run appends the sanitized routine label to the file name."""
    client = Client()
    client.set_save_enabled(True)
    client.set_save_param("file_name", "session.bvr")
    client.set_save_param("save_dir", str(tmp_path))

    # Unlimited run -> <file name>.bvr
    client.set_save_label(None)
    client._start_saving()
    assert client.data_saver is not None
    unlimited_name = client.data_saver.save_path
    client.data_saver.stop_saving()
    assert unlimited_name.endswith("session.bvr")

    # Timed run -> <file name>_<label>.bvr
    client.set_save_label("Text Routine")
    client._start_saving()
    timed_name = client.data_saver.save_path
    client.data_saver.stop_saving()
    assert timed_name.endswith("session_Text_Routine.bvr")


def test_annotation_panel_emits_signal(qapp):
    from bioview_client.components.annotate_event import AnnotateEventPanel

    panel = AnnotateEventPanel()
    received = []
    panel.annotation_requested.connect(received.append)

    # Empty text does not emit.
    panel.annotation_box.setPlainText("   ")
    panel.record_annotation()
    assert received == []

    # Non-empty text emits the trimmed annotation.
    panel.annotation_box.setPlainText("  marked event  ")
    panel.record_annotation()
    assert received == ["marked event"]

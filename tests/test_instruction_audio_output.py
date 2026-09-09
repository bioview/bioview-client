"""Routine instructions have to come out of a speaker the participant can hear.

A machine with a monitor's HDMI output alongside its real speakers has no
useful "default": Qt picks one, and if it picks the monitor the routine is
silent with nothing in the log to say so. `audio_output_device` names the output
and a name that matches nothing is reported rather than quietly ignored.
"""

import sys

from bioview_client.components.routine_control.instruction_controller import (
    resolve_audio_output,
)
from bioview_client.components.routine_control.routine import (
    parse_instruction,
    parse_timed_modes,
)


class _FakeDevice:
    def __init__(self, description):
        self._description = description

    def description(self):
        return self._description


def _patch_devices(monkeypatch, outputs, default=None):
    import bioview_client.components.routine_control.instruction_controller as mod

    class _FakeMediaDevices:
        @staticmethod
        def audioOutputs():
            return outputs

        @staticmethod
        def defaultAudioOutput():
            return default if default is not None else (outputs[0] if outputs else None)

    fake_module = type("QtMultimedia", (), {"QMediaDevices": _FakeMediaDevices})
    monkeypatch.setitem(sys.modules, "PyQt6.QtMultimedia", fake_module)
    return mod


def test_no_request_uses_the_platform_default(monkeypatch):
    speakers = _FakeDevice("Speakers (Realtek(R) Audio)")
    _patch_devices(monkeypatch, [_FakeDevice("DELL U2722DE"), speakers], speakers)

    device, note, level = resolve_audio_output(None)
    assert device is None
    assert "Realtek" in note
    assert level == "info"


def test_a_substring_picks_the_named_output(monkeypatch):
    speakers = _FakeDevice("Speakers (Realtek(R) Audio)")
    _patch_devices(monkeypatch, [_FakeDevice("DELL U2722DE"), speakers])

    device, note, level = resolve_audio_output("realtek")
    assert device is speakers
    assert "Realtek" in note
    assert level == "info"


def test_an_exact_name_beats_a_substring(monkeypatch):
    """A device whose full name is a substring of another must still win."""
    short = _FakeDevice("Speakers")
    long = _FakeDevice("Speakers (Realtek(R) Audio)")
    _patch_devices(monkeypatch, [long, short])

    device, _, _ = resolve_audio_output("Speakers")
    assert device is short


def test_a_name_that_matches_nothing_is_reported(monkeypatch):
    """The failure this exists to prevent: falling back in silence."""
    _patch_devices(monkeypatch, [_FakeDevice("DELL U2722DE")])

    device, note, level = resolve_audio_output("headphones")
    assert device is None
    assert level == "warning"
    assert "headphones" in note
    assert "DELL U2722DE" in note  # the log names what was available


def test_no_outputs_at_all_is_a_warning(monkeypatch):
    _patch_devices(monkeypatch, [])
    device, note, level = resolve_audio_output("anything")
    assert device is None
    assert level == "warning"
    assert note.startswith("No audio output")


def test_an_instruction_may_name_its_own_output():
    spec = parse_instruction(
        {"type": "audio", "file": "a.wav", "output_device": "Realtek"}
    )
    assert spec.output_device == "Realtek"


def test_an_instruction_without_one_defers_to_the_experiment():
    spec = parse_instruction({"type": "audio", "file": "a.wav"})
    assert spec.output_device is None


def test_routines_still_parse_unchanged():
    modes = parse_timed_modes(
        [
            {
                "label": "R1",
                "duration": "10s",
                "instruction": {"type": "audio", "file": "a.wav"},
            }
        ]
    )
    assert len(modes) == 1
    assert modes[0].instruction.output_device is None

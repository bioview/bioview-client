"""Presents one instruction (audio, video or text) during a timed mode.

Entirely on the Qt event loop. The owner starts it and calls ``stop()``.
"""
import contextlib
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from .instruction_dialog import InstructionDialog
from .routine import (
    INSTRUCTION_AUDIO,
    INSTRUCTION_TEXT,
    INSTRUCTION_VIDEO,
    InstructionSpec,
)


def list_audio_outputs() -> list[str]:
    """Descriptions of every host audio output Qt can play through.

    Used to log what is available: a name that matches nothing is the common
    way ``audio_output_device`` goes wrong, and the log is the only place the
    operator can see what they should have written.
    """
    try:
        from PyQt6.QtMultimedia import QMediaDevices

        return [device.description() for device in QMediaDevices.audioOutputs()]
    except Exception:  # noqa: BLE001 - QtMultimedia may be unavailable
        return []


def resolve_audio_output(requested: str | None):
    """Pick a ``QAudioDevice`` for ``requested``; returns ``(device, note, level)``.

    ``device`` is None when Qt's default should be used; ``note`` is a message
    worth logging at ``level``.

    Matching is by description -- exact first, then as a case-insensitive
    substring -- so a config can say ``"headphones"`` rather than the full
    Windows device string, while a device whose whole name is a substring of
    another can still be named precisely. A request that matches nothing falls
    back to the default *and says so*, naming what was available: silently
    playing to a different speaker than the one asked for is exactly the
    failure this exists to prevent.
    """
    from PyQt6.QtMultimedia import QMediaDevices

    outputs = QMediaDevices.audioOutputs()
    if not outputs:
        return None, "No audio output device is available on this machine", "warning"

    if not requested or str(requested).strip().lower() == "default":
        default = QMediaDevices.defaultAudioOutput()
        name = default.description() if default is not None else "unknown"
        return (
            None,
            f"Playing instructions through the default output ({name})",
            "info",
        )

    wanted = str(requested).strip().casefold()
    for match in (
        lambda name: wanted == name,
        lambda name: wanted in name,
    ):
        for device in outputs:
            if match(device.description().casefold()):
                return (
                    device,
                    f"Playing instructions through {device.description()}",
                    "info",
                )

    available = ", ".join(d.description() for d in outputs)
    return (
        None,
        f"No audio output matches {requested!r}; falling back to the default. "
        f"Available outputs: {available}",
        "warning",
    )


class InstructionController(QObject):
    log_event = pyqtSignal(str, str)
    finished = pyqtSignal()  # emitted when non-looping media reaches its end

    def __init__(
        self,
        spec: InstructionSpec,
        host_widget=None,
        parent=None,
        output_device: str | None = None,
    ):
        super().__init__(parent)
        self.spec = spec
        self.host_widget = host_widget
        # Per-instruction choice wins over the experiment-wide default.
        self.output_device = getattr(spec, "output_device", None) or output_device

        # Media (audio/video)
        self._player = None
        self._audio_out = None

        # Popup (text/video)
        self._dialog = None

        # Progressive text reveal
        self._text_timer = None
        self._text_lines = []
        self._text_idx = 0

    # Public API
    def start(self):
        try:
            if self.spec.type == INSTRUCTION_AUDIO:
                self._start_media(with_video=False)
            elif self.spec.type == INSTRUCTION_VIDEO:
                self._start_media(with_video=True)
            elif self.spec.type == INSTRUCTION_TEXT:
                self._start_text()
            else:
                self.log_event.emit(
                    "error", f"Unknown instruction type: {self.spec.type}"
                )
        except Exception as e:  # noqa: BLE001 - surface any playback setup failure
            self.log_event.emit("error", f"Unable to start instruction: {e}")
            self.stop()

    def stop(self):
        if self._text_timer is not None:
            self._text_timer.stop()
            self._text_timer.deleteLater()
            self._text_timer = None

        # Torn down explicitly: letting the GC destroy a still-active
        # QMediaPlayer hangs the UI on some platforms.
        if self._player is not None:
            with contextlib.suppress(Exception):
                self._player.mediaStatusChanged.disconnect(self._on_media_status)
            with contextlib.suppress(Exception):
                self._player.errorOccurred.disconnect(self._on_player_error)
            with contextlib.suppress(Exception):
                self._player.stop()
            with contextlib.suppress(Exception):
                self._player.setSource(QUrl())
            with contextlib.suppress(Exception):
                self._player.setVideoOutput(None)
            with contextlib.suppress(Exception):
                self._player.setAudioOutput(None)
            with contextlib.suppress(Exception):
                self._player.deleteLater()
            with contextlib.suppress(Exception):
                self._audio_out.deleteLater()
            self._player = None
            self._audio_out = None

        if self._dialog is not None:
            with contextlib.suppress(Exception):
                self._dialog.close()
                self._dialog.deleteLater()
            self._dialog = None

    # Audio / video
    def _start_media(self, with_video: bool):
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

        path = Path(self.spec.file)
        if not path.exists():
            raise FileNotFoundError(f"Instruction file not found: {self.spec.file}")

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)

        # Chosen before the output is attached to the player: QAudioOutput
        # binds its device when it is set, and switching afterwards is not
        # applied to media already queued on it.
        #
        # Never fatal: a routine playing through the wrong speaker is a bad
        # session, but one that refuses to start because the outputs could not
        # be enumerated is a worse one.
        try:
            device, note, level = resolve_audio_output(self.output_device)
        except Exception as e:  # noqa: BLE001 - fall back to Qt's default
            device = None
            note = f"Could not choose an audio output: {e}"
            level = "warning"
        if device is not None:
            self._audio_out.setDevice(device)
        if note:
            self.log_event.emit(level, note)

        # QAudioOutput starts at full scale, but a previous session's volume is
        # restored on some platforms; set it explicitly so a routine is never
        # silently played at zero.
        self._audio_out.setMuted(False)
        self._audio_out.setVolume(1.0)

        self._player.setAudioOutput(self._audio_out)
        # -1 == infinite loop, 1 == play once
        self._player.setLoops(-1 if self.spec.loop else 1)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        # A decode or backend failure is otherwise completely silent: the
        # player just never produces sound.
        self._player.errorOccurred.connect(self._on_player_error)

        if with_video:
            self._dialog = InstructionDialog(
                mode=INSTRUCTION_VIDEO, parent=self.host_widget
            )
            self._player.setVideoOutput(self._dialog.video_widget)
            self._dialog.show_instruction()

        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _on_player_error(self, _error, message: str):
        self.log_event.emit(
            "error",
            f"Could not play {Path(self.spec.file).name}: "
            f"{message or 'unknown media error'}",
        )

    def _on_media_status(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self.spec.loop:
            self.finished.emit()

    # Text
    def _start_text(self):
        path = Path(self.spec.file)
        if not path.exists():
            raise FileNotFoundError(f"Instruction file not found: {self.spec.file}")

        text = path.read_text(encoding="utf-8")
        self._dialog = InstructionDialog(
            mode=INSTRUCTION_TEXT, font_size=self.spec.font_size, parent=self.host_widget
        )

        if self.spec.line_gap is None:
            # Default: show the whole file at once and hold it for the run
            self._dialog.set_text(text)
        else:
            # Reveal lines progressively, building up the full text
            self._text_lines = [ln for ln in text.splitlines() if ln.strip()]
            self._text_idx = 0
            self._reveal_next()
            self._text_timer = QTimer(self)
            self._text_timer.setInterval(max(1, int(self.spec.line_gap * 1000)))
            self._text_timer.timeout.connect(self._reveal_next)
            self._text_timer.start()

        self._dialog.show_instruction()

    def _reveal_next(self):
        if self._text_idx >= len(self._text_lines):
            if self._text_timer is not None:
                self._text_timer.stop()
            return
        shown = "\n".join(self._text_lines[: self._text_idx + 1])
        self._dialog.set_text(shown)
        self._text_idx += 1

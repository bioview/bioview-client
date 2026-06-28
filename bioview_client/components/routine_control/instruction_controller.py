"""
Drives presentation of a single instruction (audio / video / text) for the
duration of a timed mode. Everything runs on the Qt event loop -- audio and
video via ``QMediaPlayer`` and text reveal via ``QTimer`` -- so no extra
threads are needed.

The controller is intentionally agnostic about the run duration: the owner
(monitor) starts it when a timed mode begins and calls ``stop()`` when the run
ends (either the duration elapsed or the user pressed Stop).
"""
import contextlib
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from .routine import (
    INSTRUCTION_AUDIO,
    INSTRUCTION_TEXT,
    INSTRUCTION_VIDEO,
    InstructionSpec,
)
from .instruction_dialog import InstructionDialog


class InstructionController(QObject):
    log_event = pyqtSignal(str, str)
    finished = pyqtSignal()  # emitted when non-looping media reaches its end

    def __init__(self, spec: InstructionSpec, host_widget=None, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.host_widget = host_widget

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

        # Tear the media player down explicitly. Dropping the only Python
        # reference and letting the garbage collector destroy a still-active
        # QMediaPlayer is what hangs the UI on some platforms (notably the
        # macOS/ffmpeg backend) after playback ends. Disconnect first to avoid
        # re-entrant status callbacks, stop, release the source/outputs, then
        # schedule deletion on the event loop.
        if self._player is not None:
            with contextlib.suppress(Exception):
                self._player.mediaStatusChanged.disconnect(self._on_media_status)
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
        self._player.setAudioOutput(self._audio_out)
        # -1 == infinite loop, 1 == play once
        self._player.setLoops(-1 if self.spec.loop else 1)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        if with_video:
            self._dialog = InstructionDialog(mode=INSTRUCTION_VIDEO, parent=self.host_widget)
            self._player.setVideoOutput(self._dialog.video_widget)
            self._dialog.show_instruction()

        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

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

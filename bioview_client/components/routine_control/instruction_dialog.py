"""
Popup window used to present text or video instructions during a timed mode.
Audio instructions need no window and therefore do not use this dialog.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .routine import DEFAULT_TEXT_FONT_SIZE, INSTRUCTION_VIDEO


class InstructionDialog(QDialog):
    def __init__(self, mode: str = "text", font_size: int = DEFAULT_TEXT_FONT_SIZE, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Instructions")
        # Keep the instructions visible above the (fullscreen) main window
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        self.mode = mode
        self.video_widget = None
        self.label = None

        if mode == INSTRUCTION_VIDEO:
            # Imported lazily so a missing multimedia backend only matters when a
            # video instruction is actually requested.
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            self.video_widget = QVideoWidget(self)
            layout.addWidget(self.video_widget)
        else:
            self.label = QLabel("", self)
            self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.label.setWordWrap(True)
            font = self.label.font()
            font.setPointSize(int(font_size))
            self.label.setFont(font)
            layout.addWidget(self.label)

        self.setLayout(layout)
        self._size_and_center()
        self.hide()

    def _size_and_center(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(640, 480)
            return
        geo = screen.geometry()
        w = int(geo.width() * 0.5)
        h = int(geo.height() * 0.5)
        self.resize(w, h)
        self.move(geo.x() + (geo.width() - w) // 2, geo.y() + (geo.height() - h) // 2)

    def set_text(self, text: str):
        if self.label is not None:
            self.label.setText(text)

    def show_instruction(self):
        self.show()
        self.raise_()
        self.activateWindow()

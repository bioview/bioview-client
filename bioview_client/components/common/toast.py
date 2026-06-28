from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget


class Toast(QLabel):
    """A lightweight, auto-disappearing notification overlaid on a parent widget.

    Far less intrusive than a modal dialog: it appears near the bottom of the
    parent, holds briefly, fades out, and cleans itself up.
    """

    _LEVEL_COLORS = {
        "success": "rgba(40, 167, 69, 235)",
        "info": "rgba(40, 80, 160, 235)",
        "warning": "rgba(200, 140, 0, 235)",
        "error": "rgba(176, 42, 55, 235)",
    }

    def __init__(self, parent: QWidget, message: str, level: str = "success",
                 duration_ms: int = 2200):
        super().__init__(parent)
        bg = self._LEVEL_COLORS.get(level, self._LEVEL_COLORS["info"])

        self.setText(message)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg};
                color: white;
                padding: 10px 18px;
                border-radius: 8px;
                font-size: 13px;
            }}
            """
        )

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(1.0)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(450)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade.finished.connect(self.deleteLater)

        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()

        QTimer.singleShot(max(0, duration_ms), self._fade.start)

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        rect = parent.rect()
        self.setMaximumWidth(max(160, int(rect.width() * 0.8)))
        self.adjustSize()
        x = rect.center().x() - self.width() // 2
        y = rect.bottom() - self.height() - 28
        self.move(max(8, x), max(8, y))

    @classmethod
    def show_message(cls, parent: QWidget, message: str, level: str = "success",
                     duration_ms: int = 2200) -> "Toast":
        return cls(parent, message, level=level, duration_ms=duration_ms)

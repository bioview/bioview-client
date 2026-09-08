"""The log panel, shared by the Monitor and the Configurator."""

import html
import logging

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QTextEdit,
    QVBoxLayout,
)


#: Level colours, applied to the level tag rather than the whole line so the
#: message itself stays readable in either theme.
LEVEL_COLORS = {
    "critical": "#ff6b6b",
    "error": "#ff6b6b",
    "warning": "#ffd166",
    "info": "#8ecae6",
    "debug": "#9aa0a6",
}

_TIMESTAMP_COLOR = "#7a7f87"


class QTextEditLogger(QObject, logging.Handler):
    """A logging handler that appends to a text box on the UI thread."""

    update_log = pyqtSignal(str)
    #: Level name of every record handled, for callers that badge unseen ones.
    record_logged = pyqtSignal(str)

    def __init__(self, text_box):
        super().__init__()
        self.text_box = text_box
        self.update_log.connect(self.append_text)
        self.flushOnClose = False

    def emit(self, record):
        self.update_log.emit(self.format(record))
        self.record_logged.emit(record.levelname.lower())

    def append_text(self, text):
        self.text_box.append(text)
        self.text_box.ensureCursorVisible()


class HtmlLogFormatter(logging.Formatter):
    """Formats a record as one coloured, timestamped line."""

    def __init__(self):
        super().__init__(datefmt="%H:%M:%S")

    def format(self, record):
        level = record.levelname.lower()
        color = LEVEL_COLORS.get(level, "#d0d0d0")
        # Escaped: device names and server messages can contain angle brackets,
        # which would otherwise be swallowed as markup.
        message = html.escape(record.getMessage())
        return (
            f'<span style="color:{_TIMESTAMP_COLOR}">'
            f"{self.formatTime(record, self.datefmt)}</span> "
            f'<span style="color:{color}">[{record.levelname.upper()}]</span> '
            f"{message}"
        )


class LogDisplayPanel(QGroupBox):
    """A log view backed by ``logging``, so handlers and levels work normally."""

    #: Level name of each record shown. Emitted for every route into the panel,
    #: because it is hooked to the handler rather than to ``log_message``.
    message_logged = pyqtSignal(str)

    def __init__(self, logger=None, parent=None, title="Log", max_height=None):
        super().__init__(title, parent)

        layout = QVBoxLayout()
        self.log_text_box = QTextEdit(self)
        self.log_text_box.setReadOnly(True)
        if max_height is not None:
            self.log_text_box.setMaximumHeight(max_height)
        layout.addWidget(self.log_text_box)
        self.setLayout(layout)

        # A window that has no logger of its own still gets one, so both GUIs
        # take the same path through this widget.
        if logger is None:
            logger = logging.getLogger(f"bioview.ui.{id(self):x}")
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
        self.logger = logger

        self.log_handler = QTextEditLogger(self.log_text_box)
        self.log_handler.setFormatter(HtmlLogFormatter())
        self.log_handler.record_logged.connect(self.message_logged)
        self.logger.addHandler(self.log_handler)

    def log_message(self, level, msg):
        """Record a message at a named level. ``warn`` aliases ``warning``."""
        level = str(level).lower()
        if level == "warn":
            level = "warning"

        log_method = getattr(self.logger, level, None)
        if log_method is None:
            log_method = self.logger.info
        log_method(msg)

    #: The Configurator's own panel called this; kept so both names work.
    add_log_message = log_message

    def clear(self):
        self.log_text_box.clear()


class LogWindow(QDialog):
    """A resizable window hosting a ``LogDisplayPanel``.

    Deliberately modeless: the log is most useful *while* a run is in progress,
    and an application-modal dialog would freeze the Monitor mid-stream.
    Closing hides it rather than destroying it, so the panel keeps collecting
    records and the scrollback survives being reopened.
    """

    def __init__(self, panel, parent=None, title="Log"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setModal(False)
        self.resize(820, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.panel = panel
        layout.addWidget(panel)

    def toggle(self):
        if self.isVisible():
            self.hide()
            return False
        self.show()
        self.raise_()
        self.activateWindow()
        return True

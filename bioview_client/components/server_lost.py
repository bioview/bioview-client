"""What a window shows when its server dies underneath it.

A window whose server has gone is not usable: every device command will fail,
and the status bar going red is easy to miss mid-experiment. So this is modal
and it says what happened, rather than leaving the user to work it out from a
Start button that no longer does anything.

Restarting is offered rather than done silently, because a new server is an
*empty* server -- whatever was initialized is gone, and a recording in progress
has already stopped. That is the user's to acknowledge.
"""

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from bioview_client.workers import FunctionWorker


class ServerLostDialog(QDialog):
    """Reports a dead server and offers to start a replacement.

    ``restart`` is called off the UI thread and should raise on failure; the
    reason is shown here rather than in the log, since the log window may not
    even be open. A failure is not the end of the conversation: the user can
    try again, dismiss the dialog and carry on in a disconnected window, or
    quit outright.
    """

    #: Returned by exec() when the user chose to close the whole application.
    QUIT = QDialog.DialogCode.Accepted + 1

    def __init__(self, restart, server_name="local server", parent=None):
        super().__init__(parent)
        self._restart = restart
        self._server_name = server_name
        self._pool = QThreadPool()
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("BioView server stopped")
        self.setModal(True)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 8)

        icon = QLabel()
        icon.setPixmap(
            self.style()
            .standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical)
            .pixmap(32, 32)
        )
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        header_layout.addWidget(icon)

        self.message = QLabel(
            f"The connection to the {self._server_name} was lost, so this "
            "window can no longer talk to any devices.\n\n"
            "Starting a new server will reconnect this window. Any devices "
            "that were initialized will need to be set up again, and a "
            "recording in progress has already stopped."
        )
        self.message.setWordWrap(True)
        header_layout.addWidget(self.message, stretch=1)

        layout.addWidget(header)

        # Only shown once something has actually gone wrong, so the dialog does
        # not open with an empty space where an error might one day appear.
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ff6b6b;")
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch()

        # Offered only after a restart has failed: until then, quitting is
        # already available through the window's own close button, and a third
        # button would just make the choice look heavier than it is.
        self.quit_btn = QPushButton("Quit BioView")
        self.quit_btn.clicked.connect(self._on_quit)
        self.quit_btn.hide()
        buttons.addWidget(self.quit_btn)

        self.cancel_btn = QPushButton("Continue without a server")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)

        self.restart_btn = QPushButton("Restart server")
        self.restart_btn.setDefault(True)
        self.restart_btn.clicked.connect(self._on_restart)
        buttons.addWidget(self.restart_btn)

        layout.addLayout(buttons)

    def _on_quit(self):
        self.done(self.QUIT)

    def _on_restart(self):
        """Start a server off the UI thread, so the dialog keeps painting."""
        self.error.hide()
        self._set_busy(True)

        worker = FunctionWorker(self._restart)
        worker.signals.finished.connect(self._on_restart_succeeded)
        worker.signals.error.connect(self._on_restart_failed)
        self._pool.start(worker)

    def _set_busy(self, busy: bool):
        self.restart_btn.setText("Starting..." if busy else "Restart server")
        self.restart_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        self.quit_btn.setEnabled(not busy)

    def _on_restart_succeeded(self, _result=None):
        self._set_busy(False)
        self.accept()

    def _on_restart_failed(self, message: str):
        self._set_busy(False)
        self.error.setText(f"The server could not be started.\n{message}")
        self.error.show()
        # Now that the easy answer has failed, quitting becomes a real option
        # alongside trying again and carrying on regardless.
        self.quit_btn.show()
        self.adjustSize()

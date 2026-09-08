import qtawesome as qta
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QToolButton,
)

from bioview_client.constants import get_qcolor


class AnnotateEventPanel(QGroupBox):
    log_event = pyqtSignal(str, str)
    # The monitor validates the save target and forwards the text to the
    # active recording.
    annotation_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Mark Events", parent)

        layout = QHBoxLayout()
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)

        # A single line, not a text area: an annotation is a short marker and a
        # growing box would pull height away from the plots.
        self.annotation_box = QLineEdit(self)
        self.annotation_box.setPlaceholderText("Event description…")
        self.annotation_box.setClearButtonEnabled(True)
        self.annotation_box.returnPressed.connect(self.record_annotation)
        layout.addWidget(self.annotation_box, 1)

        self.make_annotation_button = QToolButton()
        self.make_annotation_button.setText("Mark Event")
        self.make_annotation_button.setIcon(
            qta.icon("fa6s.pen-to-square", color=get_qcolor("orange"))
        )
        self.make_annotation_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.make_annotation_button.setEnabled(True)
        self.make_annotation_button.clicked.connect(self.record_annotation)
        layout.addWidget(self.make_annotation_button)

        self.setLayout(layout)
        # One control row tall and no taller, so the panel lines up with the
        # Control panel beside it instead of stretching to fill the column.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # Handle theme changes
    def _update_icons(self):
        self.make_annotation_button.setIcon(
            qta.icon("fa6s.pen-to-square", color=get_qcolor("orange"))
        )

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def record_annotation(self):
        """Emit the current annotation text for the monitor to store in the
        active recording. The box is only cleared once the monitor confirms the
        annotation was accepted (see clear_annotation)."""
        annotation = self.annotation_box.text().strip()
        if not annotation:
            self.log_event.emit("warning", "Enter some text before marking an event")
            return

        self.annotation_requested.emit(annotation)

    def clear_annotation(self):
        """Clear the text box after a successful annotation."""
        self.annotation_box.clear()

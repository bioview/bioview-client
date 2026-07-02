import qtawesome as qta
from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QPlainTextEdit, QToolButton
from PyQt6.QtCore import Qt, pyqtSignal, QEvent

from bioview_client.constants import get_qcolor

class AnnotateEventPanel(QGroupBox):
    log_event = pyqtSignal(str, str)
    # Emitted with the annotation text when the user marks an event. The monitor
    # validates the save target and forwards it to the active recording so it is
    # stored centrally in the .bvr file's "Annotations" metadata.
    annotation_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__("Mark Events", parent)

        layout = QHBoxLayout()
        self.annotation_box = QPlainTextEdit(self)
        self.annotation_box.setReadOnly(False)
        layout.addWidget(self.annotation_box)

        self.make_annotation_button = QToolButton()
        self.make_annotation_button.setText("Mark Event")
        self.make_annotation_button.setIcon(
            qta.icon("fa6s.pen-to-square", color=get_qcolor("orange"))
        )
        self.make_annotation_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextUnderIcon
        )
        self.make_annotation_button.setEnabled(True)
        self.make_annotation_button.clicked.connect(self.record_annotation)
        layout.addWidget(self.make_annotation_button)

        self.setLayout(layout)

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
        annotation = self.annotation_box.toPlainText().strip()
        if not annotation:
            self.log_event.emit("warning", "Enter some text before marking an event")
            return

        self.annotation_requested.emit(annotation)

    def clear_annotation(self):
        """Clear the text box after a successful annotation."""
        self.annotation_box.clear()

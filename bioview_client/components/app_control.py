import qtawesome as qta
from bioview_common import ClientStatus
from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QPushButton

from bioview_client.constants import get_qcolor


ROUTINE_PLACEHOLDER = "Run Routine\u2026"


class AppControlPanel(QGroupBox):
    initialize_devices = pyqtSignal()
    start_streaming = pyqtSignal()
    stop_streaming = pyqtSignal()
    enable_data_saving = pyqtSignal(bool)
    routine_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__("Control", parent)
        self.main_window = parent
        self._has_routines = False
        self._suppress_routine_signal = False

        layout = QHBoxLayout()

        self.initialize_button = QPushButton("Initialize")
        self.initialize_button.setIcon(
            qta.icon("fa6s.house", color=get_qcolor("purple"))
        )
        self.initialize_button.clicked.connect(self.on_initialize_clicked)
        layout.addWidget(self.initialize_button)

        self.start_button = QPushButton("Start")
        self.start_button.setIcon(qta.icon("fa6s.play", color=get_qcolor("green")))
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.on_start_clicked)
        layout.addWidget(self.start_button)

        self.routine_dropdown = QComboBox()
        self.routine_dropdown.setToolTip("Run a pre-defined timed routine")
        self.routine_dropdown.addItem(ROUTINE_PLACEHOLDER)
        self.routine_dropdown.setEnabled(False)
        self.routine_dropdown.setVisible(False)
        self.routine_dropdown.currentIndexChanged.connect(self.on_routine_changed)
        layout.addWidget(self.routine_dropdown)

        self.save_checkbox = QCheckBox(" Save ?")
        self.save_checkbox.clicked.connect(self.on_save_toggled)
        layout.addWidget(self.save_checkbox)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(qta.icon("fa6s.stop", color=get_qcolor("red")))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.on_stop_clicked)
        layout.addWidget(self.stop_button)

        layout.addStretch()
        self.setLayout(layout)

    def _update_icons(self):
        self.initialize_button.setIcon(
            qta.icon("fa6s.house", color=get_qcolor("purple"))
        )
        self.start_button.setIcon(qta.icon("fa6s.play", color=get_qcolor("green")))
        self.stop_button.setIcon(qta.icon("fa6s.stop", color=get_qcolor("red")))

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def update_button_states(self, client_status: ClientStatus):
        routines_runnable = False
        match client_status:
            case ClientStatus.DEFAULT:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(False)

            case ClientStatus.SERVER_CONNECTED:
                self.initialize_button.setEnabled(True)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(True)

            case ClientStatus.DEVICES_DISCOVERED:
                self.initialize_button.setEnabled(True)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(True)

            case ClientStatus.DEVICES_CONNECTED:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(True)
                routines_runnable = True

            case ClientStatus.STREAMING:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(True)
                self.save_checkbox.setEnabled(False)

            case ClientStatus.SERVER_DISCONNECTED:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.save_checkbox.setEnabled(False)
                self.stop_button.setEnabled(False)

        self.routine_dropdown.setEnabled(self._has_routines and routines_runnable)

    def set_routines(self, labels):
        self._suppress_routine_signal = True
        self.routine_dropdown.clear()
        self.routine_dropdown.addItem(ROUTINE_PLACEHOLDER)
        for label in labels or []:
            self.routine_dropdown.addItem(label)
        self.routine_dropdown.setCurrentIndex(0)
        self._suppress_routine_signal = False
        self._has_routines = bool(labels)
        self.routine_dropdown.setVisible(self._has_routines)

    def reset_routine_selection(self):
        self._suppress_routine_signal = True
        self.routine_dropdown.setCurrentIndex(0)
        self._suppress_routine_signal = False

    def on_routine_changed(self, index: int):
        if self._suppress_routine_signal or index <= 0:
            return
        self.routine_selected.emit(index - 1)

    def on_initialize_clicked(self):
        self.initialize_devices.emit()

    def on_start_clicked(self):
        self.start_streaming.emit()

    def on_stop_clicked(self):
        self.stop_streaming.emit()

    def on_save_toggled(self):
        self.enable_data_saving.emit(self.save_checkbox.isChecked())

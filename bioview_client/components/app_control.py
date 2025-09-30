import qtawesome as qta
from bioview_common import ClientStatus
from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QGroupBox, QHBoxLayout, QPushButton

from bioview_client.constants import get_qcolor


class AppControlPanel(QGroupBox):
    # Define signals to emit changes to connection status
    initialize_devices = pyqtSignal()
    start_streaming = pyqtSignal()
    stop_streaming = pyqtSignal()
    enable_data_saving = pyqtSignal(bool)
    enable_instructions = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__("Control", parent)
        self.main_window = parent

        # Initialize UI
        layout = QHBoxLayout()

        # Connect Button
        self.initialize_button = QPushButton("Initialize")
        self.initialize_button.setIcon(
            qta.icon("fa6s.house", color=get_qcolor("purple"))
        )
        self.initialize_button.clicked.connect(self.on_initialize_clicked)
        layout.addWidget(self.initialize_button)

        # Start Button
        self.start_button = QPushButton("Start")
        self.start_button.setIcon(qta.icon("fa6s.play", color=get_qcolor("green")))
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.on_start_clicked)
        layout.addWidget(self.start_button)

        # Saving Checkbox
        self.save_checkbox = QCheckBox(" Save ?")
        self.save_checkbox.clicked.connect(self.on_save_toggled)
        layout.addWidget(self.save_checkbox)

        # Instructions Checkbox (for audio/popups, etc)
        self.instructions_checkbox = QCheckBox("Instructions ?")
        self.instructions_checkbox.clicked.connect(self.on_instructions_toggled)
        layout.addWidget(self.instructions_checkbox)

        # Stop Button
        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(qta.icon("fa6s.stop", color=get_qcolor("red")))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.on_stop_clicked)
        layout.addWidget(self.stop_button)

        # Gain/balance button (visual control placeholder)
        self.gain_balance_button = QPushButton()
        try:
            self.gain_balance_button.setIcon(
                qta.icon("fa6s.rotate", color=get_qcolor("blue"))
            )
        except Exception:
            self.gain_balance_button.setText("Balance")
        self.gain_balance_button.setEnabled(False)
        layout.addWidget(self.gain_balance_button)

        layout.addStretch()
        self.setLayout(layout)

    # Handle theme changes
    def _update_icons(self):
        self.initialize_button.setIcon(
            qta.icon("fa6s.house", color=get_qcolor("purple"))
        )
        self.start_button.setIcon(qta.icon("fa6s.play", color=get_qcolor("green")))
        self.stop_button.setIcon(qta.icon("fa6s.stop", color=get_qcolor("red")))
        self.gain_balance_button.setIcon(
            qta.icon("fa6s.rotate", color=get_qcolor("blue"))
        )

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def update_button_states(self, client_status: ClientStatus):
        match client_status:
            case ClientStatus.DEFAULT:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(False)
                self.gain_balance_button.setEnabled(False)

            case ClientStatus.SERVER_CONNECTED:
                self.initialize_button.setEnabled(True)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(True)
                self.gain_balance_button.setEnabled(False)

            case ClientStatus.DEVICES_DISCOVERED:
                self.initialize_button.setEnabled(True)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(True)
                self.gain_balance_button.setEnabled(False)

            case ClientStatus.DEVICES_CONNECTED:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                self.save_checkbox.setEnabled(True)
                self.gain_balance_button.setEnabled(True)

            case ClientStatus.STREAMING:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(True)
                self.save_checkbox.setEnabled(False)
                self.gain_balance_button.setEnabled(True)

            case ClientStatus.SERVER_DISCONNECTED:
                self.initialize_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.save_checkbox.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.gain_balance_button.setEnabled(False)

    def on_initialize_clicked(self):
        self.initialize_devices.emit()

    def on_start_clicked(self):
        self.start_streaming.emit()

    def on_stop_clicked(self):
        self.stop_streaming.emit()

    def on_save_toggled(self):
        if self.save_checkbox.isChecked():
            self.enable_data_saving.emit(True)
        else:
            self.enable_data_saving.emit(False)

    def on_instructions_toggled(self):
        if self.instructions_checkbox.isChecked():
            self.enable_instructions.emit(True)
        else:
            self.enable_instructions.emit(False)

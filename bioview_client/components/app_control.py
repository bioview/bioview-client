import qtawesome as qta
from PyQt6.QtWidgets import QGroupBox, QPushButton, QHBoxLayout, QCheckBox
from PyQt6.QtCore import pyqtSignal, QEvent

from bioview_common import DeviceStatus
from bioview_client.constants import get_qcolor


class AppControlPanel(QGroupBox):
    # Define signals to emit changes to connection status
    connect_devices = pyqtSignal()
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
        self.connect_button = QPushButton("Connect")
        self.connect_button.setIcon(qta.icon("fa6s.house", color=get_qcolor("purple")))
        self.connect_button.clicked.connect(self.on_connect_clicked)
        layout.addWidget(self.connect_button)

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

        layout.addStretch()
        self.setLayout(layout)

    # Handle theme changes
    def _update_icons(self):
        self.connect_button.setIcon(qta.icon("fa6s.house", color=get_qcolor("purple")))
        self.start_button.setIcon(qta.icon("fa6s.play", color=get_qcolor("green")))
        self.stop_button.setIcon(qta.icon("fa6s.stop", color=get_qcolor("red")))
        self.gain_balance_button.setIcon(
            qta.icon("fa6s.rotate", color=get_qcolor("blue"))
        )

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def update_button_states(self, device_status):
        if device_status == DeviceStatus.NOINIT:
            self.connect_button.setEnabled(True)
            self.gain_balance_button.setEnabled(False)

        elif device_status == DeviceStatus.CONNECTING:
            self.connect_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)

        elif device_status == DeviceStatus.CONNECTED:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.save_checkbox.setEnabled(True)
            self.connect_button.setEnabled(False)

        elif device_status == DeviceStatus.STREAMING:
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.save_checkbox.setEnabled(False)
            self.gain_balance_button.setEnabled(True)

        elif device_status == DeviceStatus.DISCONNECTED:
            self.connect_button.setEnabled(True)
            self.start_button.setEnabled(False)
            self.save_checkbox.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.gain_balance_button.setEnabled(False)

        else: 
            # TODO: Log error 
            pass 

    def on_connect_clicked(self):
        self.connect_devices.emit()

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

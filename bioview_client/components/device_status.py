from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from bioview_client.constants import ConnectionStatus


class StatusIndicator(QWidget):
    """Indicate device status using the following codes -
    Connected: Green ,
    Connecting: Yellow,
    Disconnected: Red
    """

    def __init__(self, state=ConnectionStatus.DISCONNECTED, size: int = 12):
        super().__init__()
        self.state = state
        self.size = size
        self.setFixedSize(size, size)

        self.update_state(state)

    def update_state(self, state):
        self.state = state
        self.repaint()

    def paintEvent(self, event):
        # Draw the LED circle with appropriate color
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = self.state.value[1]

        painter.setBrush(color)
        painter.setPen(QPen(QColor(50, 50, 50), 1))

        margin = 1
        painter.drawEllipse(
            margin, margin, self.size - 2 * margin, self.size - 2 * margin
        )


class DeviceStatusWidget(QWidget):
    def __init__(self, device_name, device_state=ConnectionStatus.DISCONNECTED):
        super().__init__()
        self.device_name = device_name
        self.device_state = device_state

        # Create horizontal layout
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(5)

        self.label = QLabel(device_name)
        self.indicator = StatusIndicator(device_state)

        # Add widgets to layout
        layout.addWidget(self.label)
        layout.addWidget(self.indicator)

        self.setLayout(layout)

    def update_state(self, new_state):
        self.device_state = new_state
        self.indicator.update_state(new_state)


class DeviceStatusPanel(QWidget):
    def __init__(self, devices):
        super().__init__()
        self.devices = devices.copy()
        self.device_widgets = {}

        # Create horizontal layout for all devices
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(15)

        # Add server status 
        self.server_status = QLabel("Server: Disconnected")
        self.server_status.setStyleSheet("color: red; font-weight: bold;")
        self.layout.addWidget(self.server_status)
        self.layout.addWidget(QLabel("|"))
        
        # Add device widgets
        for device_name, device_map in self.devices.items():
            device_state = device_map["state"]
            self.add_device(device_name, device_state)

        self.setLayout(self.layout)

    def update_server_status(self, connected):
        """Update server status"""
        if connected:
            self.server_status.setText("Server: Connected")
            self.server_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.server_status.setText("Server: Disconnected")
            self.server_status.setStyleSheet("color: red; font-weight: bold;")
    
    # Handle theme changes
    def _update_icons(self):
        for device_name, device_state in self.devices.items():
            self.device_widgets[device_name].update_state(device_state)

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def add_device(self, device_name, device_state=ConnectionStatus.DISCONNECTED):
        device_widget = DeviceStatusWidget(device_name, device_state)
        self.device_widgets[device_name] = device_widget
        self.layout.addWidget(device_widget)
        self.devices[device_name] = device_state

    def update_device_state(self, device_name, new_state):
        if device_name in self.device_widgets.keys():
            self.device_widgets[device_name].update_state(new_state)
            self.devices[device_name] = new_state

    def remove_device(self, device_name):
        if device_name in self.device_widgets:
            widget = self.device_widgets[device_name]
            self.layout.removeWidget(widget)
            widget.deleteLater()
            del self.device_widgets[device_name]
            del self.devices[device_name]

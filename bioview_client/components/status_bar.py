import contextlib
import socket
from typing import Dict, List

import qtawesome as qta
from bioview_common import DeviceStatus
from bioview_common.protocol.status import ClientStatus
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QWidget,
)

from bioview_client.constants.theme import get_qcolor


class ServerConnector(QWidget):
    """GUI Component which displays available/connected servers and provides for an option to swap out among servers.
    Server connection state is emitted back to the main app for further co-ordination.
    """

    network_scan_requested = pyqtSignal()
    network_scan_cancel_requested = pyqtSignal()
    server_connection_requested = pyqtSignal(dict)
    server_disconnection_requested = pyqtSignal()
    # Request that the central StatusBar update server-related UI state.
    # Emitting a ClientStatus here ensures all button state changes happen
    # in StatusBar.set_server_status and nowhere else.
    server_status_change_requested = pyqtSignal(object)
    # Emitted when the user requests device discovery on the currently selected server
    discover_devices_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Track servers
        self.selected_server = {}
        self.discovered_servers = []

        # Internal state
        self._scanning = False

        # Setup UI
        self.init_ui()

        # Show local network info
        self.local_ip, self.network_ip, self.hostname = self.get_local_network_info()

    def init_ui(self):
        """Setup the user interface"""
        # Control panel
        control_layout = QHBoxLayout(self)
        control_layout.setContentsMargins(4, 0, 4, 0)

        # Scan button (toggles to cancel while scanning)
        self.scan_btn = QPushButton()
        try:
            self.scan_btn.setIcon(qta.icon("fa6s.search", color=get_qcolor("blue")))
        except Exception:
            self.scan_btn.setText("🔍")
        self.scan_btn.setToolTip("Scan network for BioView servers")
        self.scan_btn.clicked.connect(self.scan_network)
        control_layout.addWidget(self.scan_btn)

        # Server dropdown (populated by scan)
        self.server_dropdown = QComboBox()
        self.server_dropdown.setEnabled(False)
        # make slightly wider to show hostnames
        with contextlib.suppress(Exception):
            self.server_dropdown.setMinimumWidth(260)

        self.server_dropdown.currentIndexChanged.connect(self.on_server_selected)
        control_layout.addWidget(self.server_dropdown)

        # Connect button
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_server)
        self.connect_btn.setEnabled(False)
        control_layout.addWidget(self.connect_btn)

        # Disconnect button
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_from_server)
        self.disconnect_btn.setEnabled(False)
        control_layout.addWidget(self.disconnect_btn)

        # Discover devices button
        self.discover_btn = QPushButton("Discover Devices")
        self.discover_btn.clicked.connect(self.discover_devices)
        self.discover_btn.setEnabled(False)
        control_layout.addWidget(self.discover_btn)

        # Progress bar (hidden until a scan starts)
        self.scan_progress_bar = QProgressBar()
        self.scan_progress_bar.setTextVisible(False)
        self.scan_progress_bar.setFixedHeight(14)
        self.scan_progress_bar.setRange(0, 100)
        self.scan_progress_bar.setValue(0)
        self.scan_progress_bar.setVisible(False)
        control_layout.addWidget(self.scan_progress_bar)

        self.connection_label = QLabel("Status: Disconnected")
        self.connection_label.setContentsMargins(6, 0, 6, 0)
        control_layout.addWidget(self.connection_label)

        self.setLayout(control_layout)

    def scan_network(self):
        """Start network scan for servers"""
        # Toggle scanning state: if already scanning, request cancel
        if self._scanning:
            # request cancel
            self.network_scan_cancel_requested.emit()
            return

        # begin scan
        self._scanning = True
        # swap icon to stop glyph
        try:
            self.scan_btn.setIcon(qta.icon("fa6s.stop", color=get_qcolor("red")))
        except Exception:
            with contextlib.suppress(Exception):
                self.scan_btn.setText("⏹")

        # Clear choices and show progress; centralize button enablement
        # through the StatusBar.set_server_status API.
        self.server_dropdown.clear()

        # Request central UI to enter SCANNING state
        self.server_status_change_requested.emit(ClientStatus.SCANNING)
        self.scan_progress_bar.setVisible(True)

        # Ask handler to start scanning
        self.network_scan_requested.emit()

    def update_scan_progress(self, progress):
        self.scan_progress_bar.setValue(progress)

    def on_scan_complete(self, discovered_servers: List[Dict] = None):
        """On completion of network scan, handler passes along list of discovered servers"""
        # stop scanning visuals
        self._scanning = False
        try:
            self.scan_btn.setIcon(qta.icon("fa6s.search", color=get_qcolor("blue")))
        except Exception:
            with contextlib.suppress(Exception):
                self.scan_btn.setText("🔍")
        self.scan_progress_bar.setVisible(False)

        # populate dropdown only if we have results
        if discovered_servers is None or len(discovered_servers) == 0:
            self.server_dropdown.setEnabled(False)
            self.discovered_servers = []
            # Ask the central StatusBar to update all button state
            self.server_status_change_requested.emit(ClientStatus.SERVER_DISCONNECTED)
            return

        # Save discovered servers and populate by hostname when available
        self.discovered_servers = discovered_servers
        self.server_dropdown.clear()
        for server in discovered_servers:
            hostname = server.get("hostname") or ""
            address = server.get("address") or ""
            if hostname and hostname != address:
                name = f"{hostname} ({address})"
            else:
                name = address or hostname or str(server)
            self.server_dropdown.addItem(name)

        # enable connect only when we have choices -- centralize via StatusBar
        self.server_dropdown.setEnabled(True)
        # Notify the StatusBar about the available-but-not-connected state
        self.server_status_change_requested.emit(ClientStatus.SERVER_DISCONNECTED)

    def on_server_selected(self, server_index):
        if server_index < len(self.discovered_servers):
            self.selected_server = self.discovered_servers[server_index]

    def connect_to_server(self):
        """Ask handler to connect to server"""
        if self.selected_server:
            # Use .emit to emit a PyQt signal (don't call the signal like a function)
            self.server_connection_requested.emit(self.selected_server)
        else:
            # self.status ("Invalid server selected.")
            return

    # UI state is controlled centrally by StatusBar; the handler should
    # emit a real CLIENT status update which will be applied via
    # StatusBar.set_server_status. Avoid local optimistic changes here.

    def disconnect_from_server(self):
        """Disconnect from the data server"""
        # Emit the request for the handler to disconnect
        self.server_disconnection_requested.emit()

        # Ask the central StatusBar to reflect a disconnected server state.
        self.server_status_change_requested.emit(ClientStatus.SERVER_DISCONNECTED)

    def get_local_network_info(self):
        """Get local network information for display"""
        try:
            # Get local IP
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            sock.close()

            # Extract network
            ip_parts = local_ip.split(".")
            network = ".".join(ip_parts[:3]) + ".0/24"

            # Get hostname
            hostname = socket.gethostname()

            return local_ip, network, hostname
        except Exception:
            return None, None, None

    # Legacy text-based update method removed. Use set_server_status(ClientStatus) instead.

    def discover_devices(self):
        """Emit a signal requesting device discovery from the handler."""
        self.discover_devices_requested.emit()

    def closeEvent(self, event):
        """Handle widget close"""
        # Best-effort cleanup
        with contextlib.suppress(Exception):
            self.network_scan_cancel_requested.emit()
        super().closeEvent(event)


class StatusIndicator(QWidget):
    """Indicate device status using the following codes -
    Connected: Green,
    Connecting: Yellow,
    Disconnected: Red
    """

    def __init__(self, state=DeviceStatus.DISCONNECTED, size: int = 12):
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
    def __init__(self, device_name, device_state=DeviceStatus.DISCONNECTED):
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

        # Add device widgets
        for device_name, device_map in self.devices.items():
            device_state = device_map["state"]
            self.add_device(device_name, device_state)

        self.setLayout(self.layout)

    # Handle theme changes
    def _update_icons(self):
        for device_name, device_state in self.devices.items():
            self.device_widgets[device_name].update_state(device_state)

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def add_device(self, device_name, device_state=DeviceStatus.DISCONNECTED):
        device_widget = DeviceStatusWidget(device_name, device_state)
        self.device_widgets[device_name] = device_widget
        self.layout.addWidget(device_widget)
        self.devices[device_name] = device_state

    def update_device_state(self, device_name, new_state):
        if device_name in self.device_widgets:
            self.device_widgets[device_name].update_state(new_state)
            self.devices[device_name] = new_state

    def remove_device(self, device_name):
        if device_name in self.device_widgets:
            widget = self.device_widgets[device_name]
            self.layout.removeWidget(widget)
            widget.deleteLater()
            del self.device_widgets[device_name]
            del self.devices[device_name]


class StatusBar(QStatusBar):
    network_scan_requested = pyqtSignal()

    def __init__(self, parent=...):
        super().__init__(parent)

        # Use a QWidget with a layout to group widgets
        self.container = QWidget()
        self._layout = QHBoxLayout(self.container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.server_connector = ServerConnector()
        self._layout.addWidget(
            self.server_connector, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self._layout.addStretch()

        self.device_status_panel = DeviceStatusPanel(devices={})
        self._layout.addWidget(
            self.device_status_panel, alignment=Qt.AlignmentFlag.AlignRight
        )
        self.container.setLayout(self._layout)

        self.addPermanentWidget(self.container, stretch=1)

        # Forward signals and callbacks from components
        self._forward_signals()
        self._forward_callbacks()

    def _forward_signals(self):
        # Map and expose signals from the embedded ServerConnector so external
        # code can connect to them via StatusBar.
        self.network_scan_requested = self.server_connector.network_scan_requested

        # Cancel / control signals
        self.network_scan_cancel_requested = (
            self.server_connector.network_scan_cancel_requested
        )
        self.server_connection_requested = (
            self.server_connector.server_connection_requested
        )
        self.server_disconnection_requested = (
            self.server_connector.server_disconnection_requested
        )

        # Device discovery request
        self.server_discover_requested = self.server_connector.discover_devices_requested

        # Centralized server status change requests from the connector
        # are forwarded to the StatusBar.set_server_status method so that
        # all button state updates remain in one place.
        with contextlib.suppress(Exception):
            self.server_connector.server_status_change_requested.connect(
                lambda status: self.set_server_status(status)
            )

        # Expose device update helper from the panel
        self.update_device_state = self.device_status_panel.update_device_state

    def set_server_status(self, status: ClientStatus):
        """Centralize server-related UI updates based on ClientStatus."""
        try:
            if status == ClientStatus.SERVER_CONNECTED:
                self.server_connector.connection_label.setText("Status: Connected")
                self.server_connector.connection_label.setStyleSheet(
                    f"color: {get_qcolor('green').name()}"
                )
                self.server_connector.connect_btn.setEnabled(False)
                self.server_connector.disconnect_btn.setEnabled(True)
                self.server_connector.discover_btn.setEnabled(True)

            elif status == ClientStatus.SERVER_DISCONNECTED:
                self.server_connector.connection_label.setText("Status: Disconnected")
                self.server_connector.connection_label.setStyleSheet(
                    f"color: {get_qcolor('red').name()}"
                )
                # Enable connect only if dropdown has items
                has_choices = self.server_connector.server_dropdown.count() > 0
                self.server_connector.connect_btn.setEnabled(has_choices)
                self.server_connector.disconnect_btn.setEnabled(False)
                self.server_connector.discover_btn.setEnabled(False)

            else:
                # Default/other server states: set neutral text and allow connect if choices
                self.server_connector.connection_label.setText("Status: Idle")
                self.server_connector.connection_label.setStyleSheet(
                    f"color: {get_qcolor('orange').name()}"
                )
                has_choices = self.server_connector.server_dropdown.count() > 0
                self.server_connector.connect_btn.setEnabled(has_choices)
                self.server_connector.disconnect_btn.setEnabled(False)
        except Exception:
            # Best-effort: avoid UI exceptions
            pass

    def _forward_callbacks(self):
        self.on_scan_complete = self.server_connector.on_scan_complete
        self.update_scan_progress = self.server_connector.update_scan_progress

    def update_devices(self, devices: dict):
        """Replace the device panel with the discovered devices mapping."""
        try:
            old = self.device_status_panel
            self._layout.removeWidget(old)
            old.deleteLater()
        except Exception:
            pass

        self.device_status_panel = DeviceStatusPanel(devices=devices)
        self._layout.addWidget(
            self.device_status_panel, alignment=Qt.AlignmentFlag.AlignRight
        )
        self.update_device_state = self.device_status_panel.update_device_state

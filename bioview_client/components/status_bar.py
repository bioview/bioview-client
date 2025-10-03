import contextlib
from typing import Dict, List

import qtawesome as qta
from bioview_common import ClientStatus, DeviceStatus
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
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

from bioview_client.constants.theme import get_connection_status_color, get_qcolor


class ServerConnector(QWidget):
    """GUI Component which displays available/connected servers and provides
    an option to swap out among servers. Server connection state is emitted
    back to the main app for further co-ordination.
    """

    # Server-specific signals
    network_scan_requested = pyqtSignal()
    network_scan_cancel_requested = pyqtSignal()

    selected_server_changed = pyqtSignal(int)  # Pass selected server index

    server_connection_requested = pyqtSignal()
    server_disconnection_requested = pyqtSignal()

    # Device-specific signals
    discover_devices_requested = pyqtSignal()

    # Unified UI updates for state
    server_connection_state_updated = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Internal state
        self._scanning = False

        # Setup UI
        self.init_ui()

    def init_ui(self):
        """Setup the user interface"""
        # Control panel
        control_layout = QHBoxLayout(self)
        control_layout.setContentsMargins(4, 0, 4, 0)

        # Scan button (toggles to cancel while scanning)
        self.scan_btn = QPushButton()
        self._scan_icon = qta.icon("fa5s.search", color=get_qcolor("blue"))
        self._stop_icon = qta.icon("fa6s.circle-stop", color=get_qcolor("red"))

        self.scan_btn.setIcon(self._scan_icon)

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
        # swap icon to stop glyph (replace existing icon)
        self.scan_btn.setIcon(self._stop_icon)

        # Clear past results
        self.server_dropdown.clear()

        # Request central UI to enter SCANNING state
        self.server_connection_state_updated.emit(ClientStatus.SCANNING)
        self.scan_progress_bar.setVisible(True)

        # Ask handler to start scanning
        self.network_scan_requested.emit()

    def update_scan_progress(self, progress):
        self.scan_progress_bar.setValue(progress)

    def on_scan_complete(self, discovered_servers: List[Dict] = None):
        """On completion of network scan, handler passes along list of discovered servers"""
        # stop scanning visuals
        self._scanning = False

        self.scan_btn.setIcon(self._scan_icon)
        self.scan_progress_bar.setVisible(False)

        # populate dropdown only if we have results
        if discovered_servers is None or len(discovered_servers) == 0:
            self.server_dropdown.setEnabled(False)

            # Ask the central StatusBar to update all button state
            self.server_connection_state_updated.emit(ClientStatus.SERVER_DISCONNECTED)
            return

        # Save discovered servers and populate by hostname when available
        self.server_dropdown.clear()
        for server in discovered_servers:
            display_name = server.get("hostname", None)

            if not display_name:
                # Fallback to IP
                display_name = server.get("ip", None)

            if not display_name:
                display_name = "Dummy Server"

            self.server_dropdown.addItem(display_name)

        # enable connect only when we have choices -- centralize via StatusBar
        self.server_dropdown.setEnabled(True)

        # Notify the StatusBar about the available-but-not-connected state
        self.server_connection_state_updated.emit(ClientStatus.SERVER_DISCONNECTED)

    def on_server_selected(self, index):
        self.selected_server_changed.emit(index)

    def connect_to_server(self):
        self.server_connection_requested.emit()

    def disconnect_from_server(self):
        # Emit the request for the handler to disconnect
        self.server_disconnection_requested.emit()
        self.server_connection_state_updated.emit(ClientStatus.SERVER_DISCONNECTED)

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
    """Small circular indicator that reflects a device's DeviceStatus.

    - CONNECTED -> solid green
    - DISCONNECTED -> solid orange
    - CONNECTING -> blinking yellow
    - STREAMING -> solid blue
    """

    def __init__(self, status: DeviceStatus = DeviceStatus.DISCONNECTED, size: int = 12):
        super().__init__()
        self.status = status
        self.size = size
        self.setFixedSize(size, size)

        # Blinking support for CONNECTING state
        self._blink_on = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._on_blink)

        # Initialize
        self.update_status(status)

    def update_status(self, status: DeviceStatus):
        self.status = status

        # Start/stop blinking for CONNECTING
        if self.status == DeviceStatus.CONNECTING:
            if not self._blink_timer.isActive():
                self._blink_on = True
                self._blink_timer.start()
        else:
            if self._blink_timer.isActive():
                self._blink_timer.stop()
                self._blink_on = False

        # Request repaint
        self.update()

    def _on_blink(self):
        self._blink_on = not self._blink_on
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Base color from theme mapping (QColor)
            base_color = get_connection_status_color(self.status)

            # For CONNECTING state, hide when blink is off
            if self.status == DeviceStatus.CONNECTING and not self._blink_on:
                return

            painter.setBrush(base_color)
            painter.setPen(QPen(QColor(50, 50, 50), 1))

            margin = 1
            painter.drawEllipse(
                margin, margin, self.size - 2 * margin, self.size - 2 * margin
            )
        finally:
            with contextlib.suppress(Exception):
                painter.end()


class DeviceStatusWidget(QWidget):
    def __init__(self, device_name, device_status=DeviceStatus.DISCONNECTED):
        super().__init__()
        self.device_name = device_name
        self.device_status = device_status

        # Create horizontal layout
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(5)

        self.label = QLabel(device_name)
        self.indicator = StatusIndicator(device_status)

        # Add widgets to layout
        layout.addWidget(self.label)
        layout.addWidget(self.indicator)

        self.setLayout(layout)

    def update_status(self, new_status):
        self.device_status = new_status
        self.indicator.update_status(new_status)


class DeviceStatusPanel(QWidget):
    def __init__(self, device_status: Dict):
        super().__init__()
        """
        device_status is of form -
        group_id: {
            device_id: DeviceStatus
        }
        """
        self.device_widgets = {}

        # Create horizontal layout for all devices
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(15)

        # Add device widgets (keys are device ids)
        for group_id, group_items in device_status.items():
            for item_id, item_state in group_items.items():
                self.add_device(group_id, item_id, item_state)

        self.setLayout(self.layout)

    # Handle theme changes
    def _update_icons(self):
        for device_id, device_map in self.devices.items():
            with contextlib.suppress(Exception):
                status = device_map.get("status", DeviceStatus.DISCONNECTED)
                self.device_widgets[device_id] = status

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def add_device(self, group_id, device_id, device_status=DeviceStatus.DISCONNECTED):
        key = f"[{group_id}]:{device_id}"

        device_widget = DeviceStatusWidget(device_id, device_status)
        self.device_widgets[key] = device_widget
        self.layout.addWidget(device_widget)

    def update_device_status(self, group_id, device_id, new_status):
        key = f"[{group_id}]:{device_id}"
        widget = self.device_widgets.get(key, None)

        if widget:
            widget.update_status(new_status)

    def remove_device(self, group_id, device_id):
        key = f"[{group_id}]:{device_id}"
        if key in self.device_widgets:
            widget = self.device_widgets[key]
            self.layout.removeWidget(widget)
            widget.deleteLater()
            del self.device_widgets[key]


class StatusBar(QStatusBar):
    network_scan_requested = pyqtSignal()

    def __init__(self, device_status: Dict = None, parent=...):
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

        self.device_status_panel = DeviceStatusPanel(device_status=device_status)

        self._layout.addWidget(
            self.device_status_panel, alignment=Qt.AlignmentFlag.AlignRight
        )

        self.container.setLayout(self._layout)

        self.addPermanentWidget(self.container, stretch=1)

        # Forward signals and callbacks from components
        self._forward_signals()
        self._forward_callbacks()

        # Update UI to reflect connection state
        self.server_connector.server_connection_state_updated.connect(
            lambda status: self.set_server_status(status)
        )

    def _forward_signals(self):
        # Map and expose signals from the embedded ServerConnector so external
        # code can connect to them via StatusBar.
        self.network_scan_requested = self.server_connector.network_scan_requested

        # Cancel / control signals
        self.network_scan_cancel_requested = (
            self.server_connector.network_scan_cancel_requested
        )

        self.selected_server_changed = self.server_connector.selected_server_changed

        self.server_connection_requested = (
            self.server_connector.server_connection_requested
        )
        self.server_disconnection_requested = (
            self.server_connector.server_disconnection_requested
        )

        # Device discovery request
        self.discover_devices_requested = (
            self.server_connector.discover_devices_requested
        )

        # Expose device update helper from the panel
        self.update_device_status = self.device_status_panel.update_device_status

    def set_server_status(self, status: ClientStatus):
        """Centralize server-related UI updates based on ClientStatus."""
        with contextlib.suppress(Exception):
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

    def _forward_callbacks(self):
        self.on_scan_complete = self.server_connector.on_scan_complete
        self.update_scan_progress = self.server_connector.update_scan_progress

    def update_device_status(self, group_id, device_id, new_status):
        self.device_status_panel.update_device_status(group_id, device_id, new_status)

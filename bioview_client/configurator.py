# UI for device discovery
import sys
import time

from bioview_common import APP_VERSION
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bioview_client.assets import APP_DESKTOP_NAME, get_app_icon
from bioview_client.handler import Client


class DeviceConfigDialog(QDialog):
    config_changed = pyqtSignal(dict, dict)  # device_info, new_config

    def __init__(self, device_info, editable_properties, parent=None):
        super().__init__(parent)
        self.device_info = device_info
        self.editable_properties = editable_properties
        self.property_widgets = {}
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(
            f"Configure Device - {self.device_info.get('name', 'Unknown')}"
        )
        self.setModal(True)
        self.resize(400, 300)

        layout = QVBoxLayout(self)

        # Device info header
        info_label = QLabel(
            f"Device: {self.device_info.get('type', 'Unknown')} "
            f"(S/N: {self.device_info.get('serial', 'Unknown')})"
        )
        info_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        layout.addWidget(info_label)

        # Editable properties form
        form_layout = QFormLayout()

        for prop_name, prop_info in self.editable_properties.items():
            current_value = self.device_info.get(prop_name, prop_info.get("default", ""))

            if prop_info.get("type") == "text":
                widget = QLineEdit(str(current_value))
            else:
                # Default to text input
                widget = QLineEdit(str(current_value))

            self.property_widgets[prop_name] = widget
            display_name = prop_info.get("display_name", prop_name.title())
            form_layout.addRow(f"{display_name}:", widget)

        layout.addLayout(form_layout)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept_changes)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept_changes(self):
        new_config = {}
        for prop_name, widget in self.property_widgets.items():
            if isinstance(widget, QLineEdit):
                new_config[prop_name] = widget.text()

        self.config_changed.emit(self.device_info, new_config)
        self.accept()


class DeviceListPanel(QWidget):
    """Device list panel for device discovery and selection"""

    # Signals
    discover_requested = pyqtSignal()
    device_configure_requested = pyqtSignal(
        dict, dict
    )  # device_info, editable_properties

    def __init__(self):
        super().__init__()
        self.discovered_devices = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Device discovery group
        discovery_group = QGroupBox("Device Discovery")
        discovery_layout = QVBoxLayout(discovery_group)

        # Discovery button
        button_layout = QHBoxLayout()
        self.discover_btn = QPushButton("Discover Devices")
        self.discover_btn.clicked.connect(self.discover_requested.emit)
        button_layout.addWidget(self.discover_btn)
        button_layout.addStretch()
        discovery_layout.addLayout(button_layout)

        # Device list
        self.device_list = QListWidget()
        self.device_list.itemDoubleClicked.connect(self.on_device_double_clicked)
        discovery_layout.addWidget(self.device_list)

        # Instructions
        instructions = QLabel("Double-click a device to configure it")
        instructions.setStyleSheet("color: gray; font-style: italic;")
        discovery_layout.addWidget(instructions)

        layout.addWidget(discovery_group)

    def update_discovered_devices(self, devices):
        """Update device list"""
        self.discovered_devices = devices
        self.device_list.clear()

        if devices:
            for device in devices:
                device_name = device.get("name", "Unnamed Device")
                device_type = device.get("type", "Unknown")
                serial = device.get("serial", "Unknown")

                # Create display text
                display_text = f"{device_name}\n{device_type} | S/N: {serial}"

                # Create list item
                item = QListWidgetItem(display_text)
                item.setData(Qt.ItemDataRole.UserRole, device)

                # Add visual styling
                font = QFont()
                font.setBold(True)
                item.setFont(font)

                self.device_list.addItem(item)
        else:
            # Add placeholder item
            item = QListWidgetItem("No devices found")
            item.setFlags(Qt.ItemFlag.NoItemFlags)  # Make it non-selectable
            item.setForeground(Qt.GlobalColor.gray)
            self.device_list.addItem(item)

    def on_device_double_clicked(self, item):
        """Handle device double-click"""
        device_info = item.data(Qt.ItemDataRole.UserRole)
        if device_info:
            # Mock editable properties - in real implementation, this would come from client
            editable_properties = {
                "name": {
                    "type": "text",
                    "display_name": "Device Name",
                    "default": device_info.get("name", ""),
                },
                "alias": {
                    "type": "text",
                    "display_name": "Alias",
                    "default": device_info.get("alias", ""),
                },
            }

            self.device_configure_requested.emit(device_info, editable_properties)


class LogDisplayPanel(QWidget):
    """Compact log display panel"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("System Log")
        title_label.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setMaximumSize(60, 25)
        clear_btn.clicked.connect(self.clear_log)
        header_layout.addWidget(clear_btn)

        layout.addLayout(header_layout)

        # Log display (smaller)
        self.log_text = QTextEdit()
        self.log_text.setFont(QFont("Consolas", 8))
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)  # Make it smaller
        layout.addWidget(self.log_text)

    def add_log_message(self, level, message):
        """Add log message"""
        timestamp = time.strftime("%H:%M:%S")

        color_map = {
            "error": "#ff4444",
            "warning": "#ff8800",
            "info": "#4444ff",
            "debug": "#888888",
        }

        color = color_map.get(level.lower(), "#000000")
        formatted_msg = f'<span style="color: {color};">[{timestamp}] {level.upper()}: {message}</span>'

        self.log_text.append(formatted_msg)

        # Auto-scroll
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def clear_log(self):
        """Clear log"""
        self.log_text.clear()


class StatusPanel(QWidget):
    """Status panel"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.server_status = QLabel("Server: Disconnected")
        self.server_status.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.server_status)

        layout.addWidget(QLabel("|"))

        self.device_count = QLabel("Devices: 0")
        layout.addWidget(self.device_count)

        layout.addStretch()

    def update_server_status(self, connected):
        """Update server status"""
        if connected:
            self.server_status.setText("Server: Connected")
            self.server_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.server_status.setText("Server: Disconnected")
            self.server_status.setStyleSheet("color: red; font-weight: bold;")

    def update_device_count(self, count):
        """Update device count"""
        self.device_count.setText(f"Devices: {count}")


class DeviceDiscoveryClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.client_worker = None
        self.init_ui()
        self.setup_client()

    def init_ui(self):
        self.setWindowTitle("BioView Device Discovery")
        self.setWindowIcon(get_app_icon())
        screen = QGuiApplication.primaryScreen().geometry()
        width = screen.width()
        height = screen.height()
        self.setGeometry(
            int(0.4 * width),
            int(0.3 * height),
            500,
            500,  # Slightly smaller
        )

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)

        # Device list panel (takes most space)
        self.device_panel = DeviceListPanel()
        main_layout.addWidget(self.device_panel, stretch=3)

        # Log panel (smaller)
        self.log_panel = LogDisplayPanel()
        main_layout.addWidget(self.log_panel, stretch=1)

        # Status bar
        self.status_panel = StatusPanel()
        status_bar = QStatusBar()
        status_bar.addPermanentWidget(self.status_panel)
        self.setStatusBar(status_bar)

        # Connect signals
        self.device_panel.discover_requested.connect(self.discover_devices)
        self.device_panel.device_configure_requested.connect(self.show_device_config)

    def setup_client(self):
        """Connect to client"""
        self.client_worker = Client()

        # Connect signals
        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)
        self.client_worker.error_occurred.connect(
            lambda msg: self.log_panel.add_log_message("error", msg)
        )
        self.client_worker.log_message.connect(self.log_panel.add_log_message)
        self.client_worker.devices_discovered.connect(self.device_panel.update_discovered_devices)

        # Start client
        self.client_worker.start_client()

    def discover_devices(self):
        """Discover devices"""
        if self.client_worker:
            self.client_worker.discover_devices()
            
            self.status_panel.update_device_count(len(devices) if devices else 0)
            self.log_panel.add_log_message(
                "info", f"Discovery requested..."
            )

    def show_device_config(self, device_info, editable_properties):
        """Show device configuration dialog"""
        dialog = DeviceConfigDialog(device_info, editable_properties, self)
        dialog.config_changed.connect(self.on_device_config_changed)
        dialog.exec()

    def on_device_config_changed(self, device_info, new_config):
        """Handle device configuration change"""
        device_name = device_info.get("name", "Unknown")
        self.log_panel.add_log_message(
            "info", f"Configuration updated for device: {device_name}"
        )

        # Here you would send the configuration to the client
        if self.client_worker:
            # self.client_worker.update_device_config(device_info, new_config)
            pass

    def on_server_connected(self):
        """Handle server connection"""
        self.status_panel.update_server_status(True)
        self.log_panel.add_log_message("info", "Connected to server")

    def on_server_disconnected(self):
        """Handle server disconnection"""
        self.status_panel.update_server_status(False)
        self.status_panel.update_device_count(0)
        self.device_panel.update_discovered_devices([])
        self.log_panel.add_log_message("warning", "Disconnected from server")

    def closeEvent(self, event):
        """Handle application close"""
        if self.client_worker:
            self.client_worker.stop_client()
        event.accept()


def run_configurator(argv=None) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("BioView")
    app.setApplicationDisplayName("BioView Device Discovery")
    app.setDesktopFileName(APP_DESKTOP_NAME)
    app.setWindowIcon(get_app_icon())

    # Create and show main window
    window = DeviceDiscoveryClient()
    window.show()

    # Add startup messages
    window.log_panel.add_log_message(
        "info", f"Welcome to BioView Device Discovery Version {APP_VERSION}"
    )
    window.log_panel.add_log_message(
        "warning", "Make sure a compatible backend server is running!"
    )

    return app.exec()


def main(argv=None) -> int:
    return run_configurator(argv)


if __name__ == "__main__":
    sys.exit(main())

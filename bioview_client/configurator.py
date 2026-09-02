"""BioView Configurator.

Lists every device attached to the server and lets tweakable per-device
properties be edited -- the role NI's USRP Configuration Utility plays for
LabVIEW. Today the only editable property is a USRP's ``device_name``.

Flow: Discover lists what is attached; selecting a device enables Edit when its
backend declares editable properties; Edit opens a modal with those fields plus
Save and Cancel.

Names are stored by BioView (keyed on the radio's serial) and applied during
discovery, rather than written to device EEPROM. Configuration files, the
channel map and the serial cache all see the chosen name.
"""

import sys

from bioview_common import APP_VERSION, ClientStatus
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
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
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from bioview_client.assets import APP_DESKTOP_NAME, get_app_icon
from bioview_client.autoconnect import start_localhost_autoconnect
from bioview_client.components import (
    LogDisplayPanel,
    device_health_warning,
    device_is_healthy,
)
from bioview_client.handler import Client


class DeviceConfigDialog(QDialog):
    """Modal editor for one device's editable properties."""

    def __init__(self, device_info, editable_properties, parent=None):
        super().__init__(parent)
        self.device_info = device_info or {}
        self.editable_properties = editable_properties or {}
        self.property_widgets = {}
        self.result_config = None
        self._init_ui()

    def _init_ui(self):
        name = self.device_info.get("name", "Unknown")
        self.setWindowTitle(f"Edit Device - {name}")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        header = QLabel(
            f"{self.device_info.get('device_type', 'Unknown')}"
            f"  ·  S/N {self.device_info.get('serial', 'Unknown')}"
        )
        header.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        layout.addWidget(header)

        eeprom = self.device_info.get("eeprom_name")
        if eeprom and eeprom != name:
            hint = QLabel(f"Factory name: {eeprom}")
            hint.setStyleSheet("color: gray; font-style: italic;")
            layout.addWidget(hint)

        form = QFormLayout()
        for prop_name, spec in self.editable_properties.items():
            current = self.device_info.get(prop_name, spec.get("default", ""))
            widget = QLineEdit(str(current))
            if spec.get("max_length"):
                widget.setMaxLength(int(spec["max_length"]))
            if spec.get("help"):
                widget.setToolTip(spec["help"])
            self.property_widgets[prop_name] = widget
            form.addRow(f"{spec.get('display_name', prop_name.title())}:", widget)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ff6b6b;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self, config):
        for prop_name, spec in self.editable_properties.items():
            value = config.get(prop_name, "")
            label = spec.get("display_name", prop_name)
            if spec.get("required") and not value:
                return f"{label} cannot be empty."
            max_len = spec.get("max_length")
            if max_len and len(value) > int(max_len):
                return f"{label} must be {max_len} characters or fewer."
        return None

    def _on_save(self):
        config = {
            name: widget.text().strip() for name, widget in self.property_widgets.items()
        }
        error = self._validate(config)
        if error:
            self.error_label.setText(error)
            self.error_label.show()
            return
        self.result_config = config
        self.accept()


class DeviceListPanel(QWidget):
    """Discover / list / select, with Edit gated on backend support."""

    discover_requested = pyqtSignal()
    edit_requested = pyqtSignal(dict, dict)  # device_info, editable_properties

    def __init__(self):
        super().__init__()
        self.devices = []
        self.backends = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("Attached Devices")
        group_layout = QHBoxLayout(group)

        self.device_list = QListWidget()
        self.device_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.device_list.itemDoubleClicked.connect(
            lambda _item: self._emit_edit_request()
        )
        group_layout.addWidget(self.device_list, stretch=1)

        # Buttons stack to the right of the list: Discover on top, Edit beneath.
        button_column = QVBoxLayout()
        self.discover_btn = QPushButton("Discover Devices")
        self.discover_btn.clicked.connect(self.discover_requested.emit)
        button_column.addWidget(self.discover_btn)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self._emit_edit_request)
        button_column.addWidget(self.edit_btn)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: gray; font-style: italic;")
        self.hint_label.setWordWrap(True)
        button_column.addWidget(self.hint_label)
        button_column.addStretch()
        group_layout.addLayout(button_column)

        layout.addWidget(group)

    def set_busy(self, busy: bool):
        self.discover_btn.setEnabled(not busy)
        self.discover_btn.setText("Discovering..." if busy else "Discover Devices")
        if busy:
            self.edit_btn.setEnabled(False)

    def update_devices(self, devices, backends):
        self.devices = list(devices or [])
        self.backends = dict(backends or {})
        self.device_list.clear()

        if not self.devices:
            item = QListWidgetItem("No devices found")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(Qt.GlobalColor.gray)
            self.device_list.addItem(item)
            self._on_selection_changed()
            return

        for device in self.devices:
            name = device.get("name", "Unnamed Device")
            item = QListWidgetItem(f"{name}\n{self._device_details(device)}")
            item.setData(Qt.ItemDataRole.UserRole, device)
            font = QFont()
            font.setBold(True)
            item.setFont(font)
            if not device_is_healthy(device):
                item.setForeground(Qt.GlobalColor.red)
                item.setToolTip(
                    f"Windows reports this device as '{device.get('status')}'. "
                    "It will not open for acquisition until its driver is working."
                )
            self.device_list.addItem(item)

        self._on_selection_changed()

    def selected_device(self):
        items = self.device_list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.ItemDataRole.UserRole)

    def editable_properties_for(self, device):
        if not device:
            return {}
        backend = self.backends.get(device.get("device_type"), {})
        return backend.get("editable_properties", {}) or {}

    def _on_selection_changed(self):
        device = self.selected_device()
        schema = self.editable_properties_for(device)
        self.edit_btn.setEnabled(bool(schema))

        if device is None:
            self.hint_label.setText("Select a device to edit it.")
        elif schema:
            self.hint_label.setText("")
        else:
            self.hint_label.setText(
                f"{device.get('device_type', 'This device')} has no editable "
                "properties."
            )

    def _emit_edit_request(self):
        device = self.selected_device()
        schema = self.editable_properties_for(device)
        if device and schema:
            self.edit_requested.emit(device, schema)


class StatusPanel(QWidget):
    """Server connection state and device count."""

    def __init__(self):
        super().__init__()
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.server_label = QLabel("Server: disconnected")
        self.device_label = QLabel("Devices: 0")
        layout.addWidget(self.server_label)
        layout.addWidget(QLabel("|"))
        layout.addWidget(self.device_label)

    def update_server_status(self, connected):
        self.server_label.setText(
            "Server: connected" if connected else "Server: disconnected"
        )

    def update_device_count(self, count):
        self.device_label.setText(f"Devices: {count}")


class ConfiguratorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.client_worker = None
        self._pending_device = None
        self._init_ui()
        self._setup_client()

    def _init_ui(self):
        self.setWindowTitle("BioView Configurator")
        self.setWindowIcon(get_app_icon())
        screen = QGuiApplication.primaryScreen().geometry()
        self.setGeometry(
            int(0.35 * screen.width()), int(0.25 * screen.height()), 620, 560
        )

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.device_panel = DeviceListPanel()
        layout.addWidget(self.device_panel, stretch=3)

        self.log_panel = LogDisplayPanel()
        layout.addWidget(self.log_panel, stretch=1)

        self.status_panel = StatusPanel()
        status_bar = QStatusBar()
        status_bar.addPermanentWidget(self.status_panel)
        self.setStatusBar(status_bar)

        self.device_panel.discover_requested.connect(self.discover_devices)
        self.device_panel.edit_requested.connect(self.show_device_config)

    def _setup_client(self):
        self.client_worker = Client()

        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)
        self.client_worker.log_message.connect(self.log_panel.add_log_message)
        self.client_worker.devices_listed.connect(self.on_devices_listed)
        self.client_worker.device_list_failed.connect(self.on_device_list_failed)
        self.client_worker.device_config_updated.connect(self.on_device_config_updated)
        self.client_worker.device_config_failed.connect(self.on_device_config_failed)

        self.client_worker.start_client()

        # The Configurator is useless without a server, so latch onto a local one
        # as soon as it answers -- and stay latched, so it recovers by itself if
        # that server is ever restarted. The launcher starts one alongside this
        # window; localhost is the only server this window can use.
        self._connect_timer = start_localhost_autoconnect(
            self.client_worker, self, keep_latched=True
        )

    def discover_devices(self):
        if self.client_worker.status < ClientStatus.SERVER_CONNECTED:
            self.log_panel.add_log_message("warning", "Not connected to a server yet")
            return
        self.device_panel.set_busy(True)
        self.log_panel.add_log_message("info", "Discovering devices...")
        self.client_worker.list_devices()

    def on_devices_listed(self, devices, backends):
        self.device_panel.set_busy(False)
        self.device_panel.update_devices(devices, backends)
        self.status_panel.update_device_count(len(devices))

        for backend_type, info in (backends or {}).items():
            if not info.get("available", True):
                self.log_panel.add_log_message(
                    "warning",
                    f"{backend_type} backend unavailable: "
                    f"{info.get('error', 'unknown error')}",
                )

        # A device the OS cannot drive is still discovered and listed, so say so
        # here rather than letting acquisition fail later with no explanation.
        for device in devices:
            warning = device_health_warning(device)
            if warning:
                self.log_panel.add_log_message("warning", warning)

    def on_device_list_failed(self, message):
        self.device_panel.set_busy(False)
        self.log_panel.add_log_message("error", f"Discovery failed: {message}")

    def show_device_config(self, device_info, editable_properties):
        dialog = DeviceConfigDialog(device_info, editable_properties, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.result_config:
            return
        self._pending_device = device_info
        self.client_worker.set_device_config(device_info, dialog.result_config)

    def on_device_config_updated(self, device_info, message):
        self.log_panel.add_log_message(
            "info", f"{device_info.get('name', 'Device')}: {message}"
        )
        self._pending_device = None
        # Re-list so the new name is reflected everywhere.
        self.discover_devices()

    def on_device_config_failed(self, message):
        self._pending_device = None
        self.log_panel.add_log_message("error", f"Update failed: {message}")
        QMessageBox.warning(self, "Could not update device", message)

    def on_server_connected(self):
        self.status_panel.update_server_status(True)
        self.log_panel.add_log_message("info", "Connected to server")
        self.discover_devices()

    def on_server_disconnected(self):
        self.status_panel.update_server_status(False)
        self.status_panel.update_device_count(0)
        self.device_panel.update_devices([], {})
        self.log_panel.add_log_message("warning", "Disconnected from server")

    def closeEvent(self, event):
        if self.client_worker:
            self.client_worker.stop_client()
        event.accept()


def run_configurator(argv=None) -> int:
    import qdarktheme

    qdarktheme.enable_hi_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName("BioView")
    app.setApplicationDisplayName("BioView Configurator")
    app.setDesktopFileName(APP_DESKTOP_NAME)
    app.setWindowIcon(get_app_icon())
    qdarktheme.setup_theme(theme="dark")

    window = ConfiguratorWindow()
    window.show()

    window.log_panel.add_log_message("info", f"BioView Configurator {APP_VERSION}")

    return app.exec()


def main(argv=None) -> int:
    return run_configurator(argv)


if __name__ == "__main__":
    sys.exit(main())

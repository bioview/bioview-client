"""
BioView Monitor can be launched via CLI, with/without configuration JSONs pre-specified. They may also be launched using GUI without any specified configuration.
In case no valid configuration files are found, the app will prompt the user to provide configuration JSONs.
Regardless on any configurations, the UI will load with appropriate components/default values
"""
import logging  # TODO: Remove
import queue
import sys
from pathlib import Path
from typing import Dict, List

from bioview_common import DataSource, DeviceStatus
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from bioview_client.components import (
    AnnotateEventPanel,
    AppControlPanel,
    ConfigurationPrompt,
    LogDisplayPanel,
    PlotGrid,
    SettingsPanel,
    StatusBar,
    TextDialog,
)
from bioview_client.constants import DEFAULT_COMMON_CONFIGURATION
from bioview_client.handler import Client


class BioViewMonitor(QMainWindow):
    def __init__(
        self,
        device_config: List[Dict] = None,
        common_config: Dict = None,
    ):
        super().__init__()

        # Check for valid configuration files provided and, if None, ask user to add
        if not common_config or not device_config:
            dialog = ConfigurationPrompt()
            configurations = None

            if dialog.exec() == QDialog.DialogCode.Accepted:
                configurations = dialog.get_configurations()

            if configurations:
                common_config = configurations.get("common", None)
                device_config = configurations.get("devicez", None)
            else:
                # Generate a default common configuration
                common_config = DEFAULT_COMMON_CONFIGURATION
                device_config = {}

        # Store configurations
        self.common_config = common_config
        self.device_config = device_config

        # Store device names and states - since that's all the UI needs
        self.devices = {}

        # Keep track for display sources
        if not self.common_config.get_param("display_sources", None):
            self.common_config.set_param("display_sources", [])

        self.saving_status = False

        # Track instruction
        self.instruction_dialog = None
        self.enable_instructions = self.common_config.get_param(
            "enable_instructions", False
        )

        if (
            self.enable_instructions
            and self.common_config.get_param("instruction_type", "audio") == "text"
        ):
            self.instruction_dialog = TextDialog()

        # Set up UI
        self._init_ui()
        # Client is setup with handlers passed along
        self._setup_client()
        # Connect UI calls - including logging
        self._connect_signals()

        ### Common Threads
        self.instructions_thread = None

        # Display Data Queue
        self.display_data_queue = queue.Queue(maxsize=10000)

    def _init_ui(self):
        # Define main wndow
        self.setWindowTitle("BioView Data Monitor")
        iconDir = Path(__file__).resolve().parent.parent / "docs" / "assets" / "icon.png"

        self.setWindowIcon(QIcon(str(iconDir)))
        screen = QGuiApplication.primaryScreen().geometry()
        width = screen.width()
        height = screen.height()
        self.setGeometry(
            int(0.2 * width), int(0.1 * height), int(0.6 * width), int(0.8 * height)
        )

        # Create central widget and main layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Top shelf container
        top_layout = QHBoxLayout()

        # All controls are in one container
        controls_layout = QVBoxLayout()

        # Connect/Start/Stop/Balance Signal Buttons
        self.command_bar = AppControlPanel()
        controls_layout.addWidget(self.command_bar, stretch=1)

        # TODO: Make settings panel
        # experiment_layout = QHBoxLayout()
        # # Device Config Panel(s) - TODO: Fix
        # usrp_cfg = []
        # for device_dict in self.devices.values():
        #     if type(device_dict['config']).__name__ == 'MultiUsrpConfiguration':
        #         usrp_cfg = device_dict["config"].get_individual_configs()

        self.settings_panel = SettingsPanel(self.common_config, self.device_config)
        controls_layout.addWidget(self.settings_panel, stretch=3)

        top_layout.addLayout(controls_layout, stretch=3)

        # Metadata Panels
        self.meta_panels = QVBoxLayout()
        # Status Panel - Experiment Log goes here
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.log_display_panel = LogDisplayPanel(logger=self.logger)
        self.meta_panels.addWidget(self.log_display_panel, stretch=3)

        # Annotation Panel
        self.annotate_event_panel = AnnotateEventPanel(self.common_config)
        self.meta_panels.addWidget(self.annotate_event_panel, stretch=2)
        top_layout.addLayout(self.meta_panels, stretch=2)

        main_layout.addLayout(top_layout)

        # Plot Grid
        self.plot_grid = PlotGrid(self.common_config)
        main_layout.addWidget(self.plot_grid)

        central_widget.setLayout(main_layout)

        # Status Bar
        self.status_bar = StatusBar(self)
        self.setStatusBar(self.status_bar)

    def _setup_client(self):
        """Connect to client"""
        self.client_worker = Client(
            common_config=self.common_config, device_config=self.device_config
        )

        # Server control signals
        self.client_worker.server_scan_completed.connect(self.on_server_scan_completed)
        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)

        # Server info signals
        self.client_worker.server_scan_progress.connect(self.update_server_scan_progress)

        # Device control signals
        self.client_worker.device_connected.connect(self.on_device_connected)
        self.client_worker.device_disconnected.connect(self.on_device_disconnected)
        self.client_worker.streaming_started.connect(self.on_streaming_started)
        self.client_worker.streaming_stopped.connect(self.on_streaming_stopped)

        # Device info signals

        # General info signals
        self.client_worker.error_occurred.connect(
            lambda msg: self.log_display_panel.log_message("error", msg)
        )
        self.client_worker.log_message.connect(self.log_display_panel.log_message)

        # Data signals

        # Start client
        self.client_worker.start_client()

    def _connect_signals(self):
        """
        Connect signals from all UI components to respective calls in client worker
        """
        if self.client_worker is None:
            self._setup_client()

        # App Bar
        self.command_bar.connect_devices.connect(self.on_device_connection_requested)
        self.command_bar.start_streaming.connect(self.client_worker.start_streaming)
        self.command_bar.stop_streaming.connect(self.client_worker.stop_streaming)
        self.command_bar.enable_data_saving.connect(self.update_save_state)
        self.command_bar.enable_instructions.connect(self.toggle_instructions)

        # Settings Panel
        if getattr(self.settings_panel, "display_duration_changed", None):
            self.settings_panel.display_duration_changed.connect(
                self.handle_time_window_change
            )
        if getattr(self.settings_panel, "grid_layout_changed", None):
            self.settings_panel.grid_layout_changed.connect(
                self.handle_grid_layout_change
            )
        if getattr(self.settings_panel, "add_data_source", None):
            self.settings_panel.add_data_source.connect(self.handle_add_source)
        if getattr(self.settings_panel, "remove_data_source", None):
            self.settings_panel.remove_data_source.connect(self.handle_remove_source)

        self.settings_panel.log_event.connect(self.log_display_panel.log_message)

        # Annotate Event Panel

        # Plot Grid
        self.plot_grid.log_event.connect(self.log_display_panel.log_message)

        # Status Bar
        self.status_bar.network_scan_requested.connect(
            self.client_worker.discover_servers
        )

    def closeEvent(self, event):
        """Handle application close"""
        if self.client_worker:
            self.client_worker.stop_client()
        event.accept()

    # Handlers for UI updates
    def handle_time_window_change(self, seconds):
        self.plot_grid.set_display_time(seconds)

    def handle_grid_layout_change(self, rows, cols):
        self.plot_grid.update_grid(rows, cols)

    def handle_add_source(self, source: DataSource):
        if self.plot_grid.add_source(source):
            # Update a
            sel_channels = self.common_config.get_param("display_sources")
            sel_channels.append(source)
            self.common_config.set_param("display_sources", list(set(sel_channels)))
            # Change state of UI
            self.experiment_settings_panel.update_source("add", source)

    def handle_remove_source(self, source: DataSource):
        if self.plot_grid.remove_source(source):
            # Update config
            sel_channels = self.common_config.get_param("display_sources")
            sel_channels.remove(source)
            self.common_config.set_param("display_sources", sel_channels)
            # Change state of UI
            self.experiment_settings_panel.update_source("remove", source)

    # Status Bar helper functions
    def on_device_connection_requested(self):
        if self.client_worker:
            for device_id in self.devices:
                self.status_bar.update_device_state(device_id, DeviceStatus.CONNECTING)
                self.client_worker.connect_device(device_id=device_id)

    def update_save_state(self):
        self.saving_status = True
        if self.client_worker:
            pass

    def toggle_instructions(self, flag):
        self.enable_instructions = flag
        if self.instruction_dialog is not None:
            self.instruction_dialog.toggle_ui(self.enable_instructions)

    # Client worker helper functions
    def on_server_scan_completed(self):
        pass

    def on_server_connected(self):
        """Handle server connection"""
        self.status.update_server_status(True)
        self.log_display_panel.log_message("info", "Connected to server")

        # Auto-ping
        if self.client_worker:
            self.client_worker.ping_server()

        # TODO: Populate display sources by querying server

    def on_server_disconnected(self):
        """Handle server disconnection"""
        self.status_bar.update_server_connection_status(False)
        self.log_display_panel.log_message("warning", "Disconnected from server")

    def update_server_scan_progress(self):
        pass

    def on_device_connected(self, device_id):
        if device_id is not None:
            self.devices[device_id]["state"] = DeviceStatus.CONNECTED
            self.status_bar.update_device_state(device_id, DeviceStatus.CONNECTED)
        else:
            # In this case all devices were requested for connection
            for device_id in self.devices:
                self.devices[device_id]["state"] = DeviceStatus.CONNECTED
                self.status_bar.update_device_state(device_id, DeviceStatus.CONNECTED)

        # Check if all are connected and if so, disable UI buttons
        self.update_buttons()

    def on_device_disconnected(self):
        # Disconnect devices
        for device_id in self.devices:
            self.devices[device_id]["state"] = DeviceStatus.DISCONNECTED
            self.status_bar.update_device_state(device_id, DeviceStatus.DISCONNECTED)

        self.update_buttons()

    def on_streaming_started(self):
        pass

    def on_streaming_stopped(self):
        pass

    def update_buttons(self):
        connected = True
        for device_dict in self.devices.values():
            if device_dict["state"] == DeviceStatus.DISCONNECTED:
                connected = False
                break

        if connected:
            self.command_bar.update_button_states(DeviceStatus.CONNECTED)
        else:
            self.command_bar.update_button_states(DeviceStatus.DISCONNECTED)


if __name__ == "__main__":
    import qdarktheme  # Provide consistent styling across all OSes

    qdarktheme.enable_hi_dpi()
    app = QApplication(sys.argv)
    qdarktheme.setup_theme(theme="auto")

    # Create and show main window
    window = BioViewMonitor()
    window.show()

    sys.exit(app.exec())

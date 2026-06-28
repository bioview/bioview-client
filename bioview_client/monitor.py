"""
BioView Monitor can be launched via CLI, with/without configuration
JSONs pre-specified. They may also be launched using GUI without any
specified configuration. In case no valid configuration files are
found, the app will prompt the user to provide configuration JSONs.
Regardless of any configurations, the UI will load with appropriate
components/default values
"""
import argparse
import contextlib
import logging  # TODO: Remove
import queue
import sys
from pathlib import Path
from typing import Dict, List

from bioview_common import ClientStatus, DataSource, DeviceStatus, ExperimentConfiguration, parse_configuration_file, SUPPORTED_CONFIGURATION_TYPES
from PyQt6.QtCore import QTimer, Qt
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
from bioview_client.handler import Client

class BioViewMonitor(QMainWindow):
    """'
    The main UI can be launched with or without CL arguments.

    Inputs -
    group_configs: List[Dict] or Dict
        A list of device group configurations. Each element in the list pertains to one
        device group (dictionary). Key-value pairs in the device group correspond to
        device ID and device configurations respectively. If None, the application will
        prompt the user for configurations via a dialog.
    experiment_config: Dict
        A dictionary containing common configuration parameters. If None, the application
        will prompt the user for configurations via a dialog.
    autodiscover: bool
        If True, automatically scan for available servers on startup. Default is True.
    autoconnect: bool
        If True, attempt to connect to the first discovered server. Default is False.
    """

    def __init__(
        self,
        config_file: str | Path = None, 
        # group_configs: List[Dict] = None,
        # experiment_config: Dict = None,
        autodiscover: bool = True,
        autoconnect: bool = False,
    ):
        super().__init__()
        # Persist CLI/UI flags
        self.autodiscover = autodiscover
        self.autoconnect = autoconnect

        self.config_file = config_file 

        # If no valid configuration file present, prompt user.
        if not self.config_file: 
            dialog = ConfigurationPrompt()

            if dialog.exec() == QDialog.DialogCode.Accepted: 
                self.config_file = dialog.get_config_file() 

        # Now, parse and validate configurations. 
        self.configurations = parse_configuration_file(self.config_file)

        experiment_cfg_id = None
        for cfg_id, cfg in self.configurations.items(): 
            if cfg.get_type() == SUPPORTED_CONFIGURATION_TYPES.EXPERIMENT:
                experiment_cfg_id = cfg_id
                break
        
        if not experiment_cfg_id: 
            self.experiment_config = ExperimentConfiguration({}) # Load a default one 
            self.group_configs = self.configurations
        else: 
            self.experiment_config = self.configurations[experiment_cfg_id]
            self.group_configs = {k: v for k, v in self.configurations.items() if k != experiment_cfg_id}

        # Store group states
        self.device_status = {k: DeviceStatus.NOINIT for k in self.group_configs}

        self.saving_status = False

        # Track instruction
        self.instruction_dialog = None
        self.enable_instructions = self.experiment_config.get_param("enable_instructions", False)
        self.instructions_type = self.experiment_config.get_param("instruction_type", None)

        if self.enable_instructions: 
            if self.instructions_type == "text": 
                self.instruction_dialog = TextDialog()
            elif self.instructions_type == 'audio': 
                pass # TODO: Add Audio instructions. 

        # Set up UI
        self._init_ui()

        # Client is setup with handlers passed along
        self.client_worker = Client(
            experiment_config=self.experiment_config, group_configs=self.group_configs
        )
        self._connect_client_signals()
        self.client_worker.start_client()
        self.command_bar.update_button_states(self.client_worker.status)

        # Connect UI calls - including logging
        self._connect_signals()

        ### Common Threads
        self.instructions_thread = None

        # Available data sources advertised by the connected server
        self.available_sources = []

    def _init_ui(self):
        # Define main wndow
        self.setWindowTitle("BioView Data Monitor")
        # TODO: Make path agnostic
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

        self.settings_panel = SettingsPanel(self.configurations)

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
        self.annotate_event_panel = AnnotateEventPanel(self.experiment_config)
        self.meta_panels.addWidget(self.annotate_event_panel, stretch=2)
        top_layout.addLayout(self.meta_panels, stretch=2)

        main_layout.addLayout(top_layout)

        # Plot Grid
        self.plot_grid = PlotGrid(self.experiment_config)
        main_layout.addWidget(self.plot_grid)

        central_widget.setLayout(main_layout)

        # Status Bar
        self.status_bar = StatusBar(device_status=self.device_status, parent=self)
        self.setStatusBar(self.status_bar)

    def _connect_client_signals(self):
        """Connect client signals to UI handlers."""
        # Server control signals
        self.client_worker.server_scan_completed.connect(
            self.status_bar.on_scan_complete
        )
        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)

        # Server info signals
        self.client_worker.server_scan_progress.connect(
            self.status_bar.update_scan_progress
        )

        # Device control signals
        self.client_worker.device_init_succeeded.connect(
            self.update_status_bar_and_buttons
        )
        # Populate plot sources once devices are initialized/discovered
        self.client_worker.device_init_succeeded.connect(self.on_devices_ready)
        self.client_worker.devices_discovered.connect(self.on_devices_ready)
        self.client_worker.device_disconnect_succeeded.connect(
            self.update_status_bar_and_buttons
        )
        self.client_worker.streaming_started.connect(
            lambda x: self._handle_streaming_status_changed(x)
        )
        self.client_worker.streaming_stopped.connect(
            lambda x: self._handle_streaming_status_changed(not x)
        )
        self.client_worker.devices_discovered.connect(self.update_status_bar_and_buttons)

        # Data signal for live display (data, sources). Use a queued connection so
        # data bursts are marshalled to the UI thread one event at a time and never
        # block or re-enter the receiving path.
        self.client_worker.data_received.connect(
            self.on_data_received, Qt.ConnectionType.QueuedConnection
        )

        # General info signals
        self.client_worker.log_message.connect(self.log_display_panel.log_message)

    def _connect_signals(self):
        """
        Connect signals from all UI components to respective calls in client worker
        """
        self._connect_command_bar_signals()
        self._connect_settings_panel_signals()
        self._connect_statusbar_signals()

    def _handle_server_connection_request(self, server_info: dict):
        """Handle server connect requests from the UI"""
        if not server_info:
            return
        # Set selected server on client worker and ask it to connect
        if self.client_worker:
            self.client_worker.selected_server = server_info

            with contextlib.suppress(Exception):
                self.client_worker.connect_to_server()

    def _connect_command_bar_signals(self):
        self.command_bar.initialize_devices.connect(self.on_device_init_requested)
        self.command_bar.start_streaming.connect(self.client_worker.start_streaming)
        self.command_bar.stop_streaming.connect(self.client_worker.stop_streaming)
        self.command_bar.enable_data_saving.connect(self.update_save_state)
        self.command_bar.enable_instructions.connect(self.toggle_instructions)

    def _connect_settings_panel_signals(self):
        # Save-related parameters (file name / save dir) flow to the client worker
        if getattr(self.settings_panel, "parameter_changed", None):
            self.settings_panel.parameter_changed.connect(self.on_parameter_changed)

        if getattr(self.settings_panel, "display_duration_changed", None):
            self.settings_panel.display_duration_changed.connect(
                self.handle_time_window_change
            )
        if getattr(self.settings_panel, "grid_layout_changed", None):
            self.settings_panel.grid_layout_changed.connect(
                self.handle_grid_layout_change
            )
        if getattr(self.settings_panel, "add_data_source", None):
            self.settings_panel.add_data_source.connect(self.add_plot_source)
        if getattr(self.settings_panel, "remove_data_source", None):
            self.settings_panel.remove_data_source.connect(self.remove_plot_source)

        # Connect logging
        self.settings_panel.log_event.connect(self.log_display_panel.log_message)
        self.plot_grid.log_event.connect(self.log_display_panel.log_message)

    def _connect_statusbar_signals(self):
        # Discovery/start scan
        self.status_bar.network_scan_requested.connect(
            self.client_worker.discover_servers
        )

        # Cancel scan
        self.status_bar.network_scan_cancel_requested.connect(
            self.client_worker.cancel_scan
        )

        # Update selected server
        self.status_bar.selected_server_changed.connect(
            self.client_worker.change_selected_server
        )

        # Server connection request
        self.status_bar.server_connection_requested.connect(
            self.client_worker.connect_to_server
        )

        # Server disconnection request
        self.status_bar.server_disconnection_requested.connect(
            self.client_worker.disconnect_from_server
        )

        # Device discovery request
        self.status_bar.discover_devices_requested.connect(
            lambda: self.client_worker.initialize_devices(True)
        )

    def _handle_streaming_status_changed(self, is_streaming: bool):
        status = DeviceStatus.STREAMING if is_streaming else DeviceStatus.CONNECTED

        # device_status is a flat mapping {group_id: DeviceStatus}
        for group_id in self.device_status:
            if group_id == "metadata":
                continue

            self.device_status[group_id] = status
            self.status_bar.update_device_status(group_id, status)

        client_status = self.client_worker.status
        self.command_bar.update_button_states(client_status)

    def closeEvent(self, event):
        """Handle application close"""
        if self.client_worker:
            self.client_worker.stop_client()
        event.accept()

    # Handlers for UI updates
    def handle_time_window_change(self, seconds):
        self.plot_grid.set_display_time(seconds)

    def handle_grid_layout_change(self, rows, cols):
        # Resizing keeps still-fitting sources plotted in place; any that no
        # longer fit the new (smaller) grid are returned so we can uncheck them
        # in the source selector and keep the UI state consistent.
        dropped = self.plot_grid.update_grid(rows, cols)
        for src in dropped or []:
            self.settings_panel.update_source("remove", src)

    def populate_plot_grid_sources(self, sources):
        """Populate the plot-source selector from the data sources advertised by
        the server. `sources` may be DataSource objects or descriptor dicts."""
        if not sources:
            return

        source_objs = []
        for src in sources:
            if isinstance(src, DataSource):
                source_objs.append(src)
            elif isinstance(src, dict):
                source_objs.append(DataSource.from_dict(src))

        self.available_sources = source_objs
        self.settings_panel.set_available_sources(source_objs)

    def on_data_received(self, data, sources):
        """Route a received data chunk to the plot grid for display."""
        self.plot_grid.add_new_data(data, sources)

    def on_devices_ready(self, _device_status=None):
        """Populate the plot-source selector from the server's advertised sources."""
        data_sources = self.client_worker.get_data_sources()
        if data_sources:
            self.populate_plot_grid_sources(data_sources)

    def on_parameter_changed(self, name, value):
        """Forward experiment parameter changes (e.g. save_dir/file_name) to client."""
        if self.client_worker:
            self.client_worker.set_save_param(name, value)

    def add_plot_source(self, source: DataSource):
        """
        Connects a new data source to a PlotGrid object
        """
        if self.plot_grid.add_source(source):
            # If source can be successfully shown, mark it as selected in the panel
            self.settings_panel.update_source("add", source)

    def remove_plot_source(self, source: DataSource):
        """
        Removes an existing data source from a PlotGrid object
        """
        if self.plot_grid.remove_source(source):
            # If source can be successfully removed, deselect in the panel
            self.settings_panel.update_source("remove", source)

    # Command Bar helper functions
    def on_device_init_requested(self):
        if not self.client_worker:
            return

        # Update UI to show CONNECTING state for all known devices
        # device_status is a flat mapping {group_id: DeviceStatus}
        for group_id in self.device_status:
            if group_id == "metadata":
                continue
            self.status_bar.update_device_status(group_id, DeviceStatus.CONNECTING)

        # Request server to connect all initialized devices
        self.client_worker.initialize_devices()

    def update_save_state(self, enabled: bool = True):
        self.saving_status = bool(enabled)
        if self.client_worker:
            self.client_worker.set_save_enabled(bool(enabled))

    def toggle_instructions(self, flag):
        self.enable_instructions = flag
        if self.instruction_dialog is not None:
            self.instruction_dialog.toggle_ui(self.enable_instructions)

    # Client worker helper functions
    def on_server_connected(self, connected: bool = True):
        self.log_display_panel.log_message("info", "Connected to server")

        try:
            self.status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)
        except Exception:
            self.log_display_panel.log_message("warning", "Unable to update status bar")
            self.status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)

        self.status_bar.server_connector.discover_btn.setEnabled(True)

        self.command_bar.update_button_states(self.client_worker.status)

        # Populate plot sources from the data sources advertised by the server
        data_sources = self.client_worker.get_data_sources()
        if data_sources:
            self.populate_plot_grid_sources(data_sources)

    def on_server_disconnected(self):
        try:
            self.status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
        except Exception:
            self.log_display_panel.log_message("warning", "Unable to update status bar")

        self.command_bar.update_button_states(self.client_worker.status)
        self.log_display_panel.log_message("warning", "Disconnected from server")

    def on_streaming_started(self):
        pass

    def update_status_bar_and_buttons(self, device_status: Dict):
        # device_status is a flat mapping {group_id: DeviceStatus value}
        for group_id, new_status in device_status.items():
            if group_id == "metadata":
                continue

            if not isinstance(new_status, DeviceStatus):
                with contextlib.suppress(Exception):
                    new_status = DeviceStatus(new_status)
            self.status_bar.update_device_status(group_id, new_status)

        client_status = self.client_worker.status
        self.command_bar.update_button_states(client_status)


if __name__ == "__main__":
    import qdarktheme  # Provide consistent styling across all OSes

    parser = argparse.ArgumentParser(description="Launch BioView Monitor UI")
    parser.add_argument(
        "--config-file",
        nargs="*",
        help="In case the app is launched using a .bview file", # A .json also works. 
        default=[],
    )
    parser.add_argument(
        "--autodiscover",
        dest="autodiscover",
        action="store_true",
        help="Automatically discover servers on start (default)",
    )
    parser.add_argument(
        "--autoconnect",
        dest="autoconnect",
        action="store_true",
        help="Automatically connect to first discovered (usually localhost) server",
    )

    args = parser.parse_args()

    qdarktheme.enable_hi_dpi()
    app = QApplication(sys.argv)
    qdarktheme.setup_theme(theme="dark")

    # Create and show main window with parsed configs and flags
    window = BioViewMonitor(
        config_file=args.config_file, 
        autodiscover=args.autodiscover,
        autoconnect=args.autoconnect,
    )
    window.show()

    # If auto-discover, trigger the scan. The scan is asynchronous, so autoconnect
    # must wait for the scan to actually complete before selecting/connecting.
    if window.autodiscover and window.client_worker:
        handler = window.client_worker

        if window.autoconnect:
            def _autoconnect_when_scan_done(servers):
                # Connect to the first discovered server once results arrive. If a
                # scan finds nothing we stay subscribed so a later retry (below)
                # that finds the server will still autoconnect.
                if servers:
                    with contextlib.suppress(Exception):
                        handler.server_scan_completed.disconnect(
                            _autoconnect_when_scan_done
                        )
                    handler.change_selected_server(0)
                    handler.connect_to_server()

            handler.server_scan_completed.connect(_autoconnect_when_scan_done)

        # Periodically re-scan until a server is found / we are connected, so the
        # client can be started before the server and still discover it later.
        rescan_timer = QTimer()

        def _maybe_rescan():
            # Stop retrying once connected (or further along) or once we have
            # results the user can act on. A scan already in flight is left alone.
            if handler.status >= ClientStatus.SERVER_CONNECTED:
                rescan_timer.stop()
                return
            if handler.status == ClientStatus.SCANNING:
                return
            if handler.discovered_servers:
                # Found something already; keep retrying only in autoconnect mode
                # (so a dropped server can be re-found), otherwise let the user act.
                if not window.autoconnect:
                    rescan_timer.stop()
                    return
            handler.discover_servers()

        rescan_timer.timeout.connect(_maybe_rescan)
        rescan_timer.start(5000)
        # Keep a reference so the timer isn't garbage-collected
        window._rescan_timer = rescan_timer

        handler.discover_servers()

    sys.exit(app.exec())
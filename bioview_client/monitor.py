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

from bioview_common import ClientStatus, Configuration, DataSource, DeviceStatus
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
from bioview_client.utils import is_dict_of_dicts, load_json_file


class BioViewMonitor(QMainWindow):
    """'
    The main UI can be launched with or without CL arguments.

    Inputs -
    group_configs: List[Dict] or Dict
        A list of device group configurations. Each element in the list pertains to one
        device group (dictionary). Key-value pairs in the device group correspond to
        device ID and device configurations respectively. If None, the application will
        prompt the user for configurations via a dialog.
    common_config: Dict
        A dictionary containing common configuration parameters. If None, the application
        will prompt the user for configurations via a dialog.
    autodiscover: bool
        If True, automatically scan for available servers on startup. Default is True.
    autoconnect: bool
        If True, attempt to connect to the first discovered server. Default is False.
    """

    def __init__(
        self,
        group_configs: List[Dict] = None,
        common_config: Dict = None,
        autodiscover: bool = True,
        autoconnect: bool = False,
    ):
        super().__init__()
        # Persist CLI/UI flags
        self.autodiscover = autodiscover
        self.autoconnect = autoconnect

        # Check for valid configurations, else prompt user.
        # UI and backend store it in Configuration objects,
        # while handlers store as dict/json.
        self.common_config, self.group_configs = self._resolve_initial_configs(
            common_config, group_configs
        )

        # Store device names and states - since that's all the UI needs
        self.device_status: Dict[str, Dict] = {}
        for group_id, group_dict in self.group_configs.items():
            tmp = {}
            for item_id in group_dict:
                if item_id == "metadata":
                    continue
                tmp[item_id] = DeviceStatus.NOINIT

            self.device_status[group_id] = tmp

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
        self.client_worker = Client(
            common_config=self.common_config, group_configs=self.group_configs
        )
        self._connect_client_signals()
        self.client_worker.start_client()
        self.command_bar.update_button_states(self.client_worker.status)

        # Connect UI calls - including logging
        self._connect_signals()

        ### Common Threads
        self.instructions_thread = None

        # Display Data Queue
        self.display_data_queue = queue.Queue(maxsize=10000)

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

        self.settings_panel = SettingsPanel(self.common_config, self.group_configs)

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
        self.status_bar = StatusBar(device_status=self.device_status, parent=self)
        self.setStatusBar(self.status_bar)

    # If initial configurations do not exist, ask using inputs
    def _resolve_initial_configs(self, common_config, group_configs):
        """
        We initially receive configurations in JSON/dict format. These need
        to be sanitized (if applicable). Hence, we load them into a
        configuration object format. For the handler, however, we keep them
        as dictionaries for easy serialization.
        """
        if not common_config or not group_configs:
            dialog = ConfigurationPrompt()
            # Will provide configurations as dict objects
            configurations = None

            if dialog.exec() == QDialog.DialogCode.Accepted:
                configurations = dialog.get_configurations()

            if configurations:
                common_cfg = configurations.get("common", None)
                group_cfgs = configurations.get("groups", None)
            else:
                common_cfg = DEFAULT_COMMON_CONFIGURATION
                group_cfgs = {}

        common_cfg = Configuration.from_dict(common_config)
        group_cfgs = self._convert_group_configs(group_configs)

        # At this stage, we have sanitized Configuration objects
        return common_cfg, group_cfgs

    def _convert_group_configs(self, group_cfgs: Dict):
        if not is_dict_of_dicts(group_cfgs):
            return {}

        converted: Dict[str, Dict] = {}

        for group_id, group_items in group_cfgs.items():
            converted[group_id] = {}
            device_ids = [k for k in group_items if k != "metadata"]

            # Populate metadata for further use
            meta = group_items.get("metadata", {})
            meta["group_id"] = group_id

            for device_id in device_ids:
                device_cfg = group_items[device_id]
                converted[group_id][device_id] = Configuration.from_dict(device_cfg)

                # TODO: Make this less jank
                # Populate device_type using first device
                if "device_type" not in meta:
                    meta["device_type"] = converted[group_id][device_id].get_param(
                        "device_type"
                    )

                # Populate samp_rate using first device
                if "samp_rate" not in meta:
                    meta["samp_rate"] = converted[group_id][device_id].get_param(
                        "samp_rate"
                    )

            converted[group_id]["metadata"] = meta

        return converted

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

    def _handle_devices_discovered(self, devices_map: dict):
        """Update status bar device panel when client reports discovered devices."""
        if not devices_map:
            return

        # Enforce strict group->device mapping for the status bar. Reject other shapes.
        if not is_dict_of_dicts(devices_map):
            self.log_display_panel.log_message(
                "warning",
                "Received device map was not formatted correctly and was ignored.",
            )
            return

        self.device_status = devices_map
        self.update_status_bar_and_buttons(devices_map)

    def _handle_streaming_status_changed(self, is_streaming: bool):
        status = DeviceStatus.STREAMING if is_streaming else DeviceStatus.CONNECTED

        for group_id, group in self.device_status.items():
            for device_id in group:
                if device_id == "metadata":
                    continue

                self.device_status[group_id][device_id] = status
                self.status_bar.update_device_status(group_id, device_id, status)

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
        self.plot_grid.update_grid(rows, cols)

    def populate_plot_grid_sources(self, sources):
        # TODO: Callback from handler
        pass

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
        for group_id, group in self.device_status.items():
            for device_id in group:
                self.status_bar.update_device_status(
                    group_id, device_id, DeviceStatus.CONNECTING
                )

        # Request server to connect all initialized devices
        self.client_worker.initialize_devices()

    def update_save_state(self):
        self.saving_status = True
        if self.client_worker:
            pass

    def toggle_instructions(self, flag):
        self.enable_instructions = flag
        if self.instruction_dialog is not None:
            self.instruction_dialog.toggle_ui(self.enable_instructions)

    # Client worker helper functions
    def on_server_connected(self):
        self.log_display_panel.log_message("info", "Connected to server")

        try:
            self.status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)
        except Exception:
            self.log_display_panel.log_message("warning", "Unable to update status bar")
            self.status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)

        self.status_bar.server_connector.discover_btn.setEnabled(True)

        self.command_bar.update_button_states(self.client_worker.status)

        # TODO: Populate display sources by querying server

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
        for group_id, group in device_status.items():
            for device_id, new_status in group.items():
                if device_id == "metadata":
                    continue

                if not isinstance(new_status, DeviceStatus):
                    new_status = DeviceStatus(new_status)
                self.status_bar.update_device_status(group_id, device_id, new_status)

        client_status = self.client_worker.status
        self.command_bar.update_button_states(client_status)


if __name__ == "__main__":
    import qdarktheme  # Provide consistent styling across all OSes

    parser = argparse.ArgumentParser(description="Launch BioView Monitor UI")
    parser.add_argument(
        "--devices",
        nargs="*",
        help="List of device configuration JSON files",
        default=None,
    )
    parser.add_argument(
        "--common",
        help="Common configuration JSON file",
        default=None,
    )
    parser.add_argument(
        "--autodiscover",
        dest="autodiscover",
        action="store_true",
        help="Automatically discover servers on start (default)",
    )
    parser.add_argument(
        "--no-autodiscover",
        dest="autodiscover",
        action="store_false",
        help="Do not automatically discover servers on start",
    )
    parser.set_defaults(autodiscover=True)

    parser.add_argument(
        "--autoconnect",
        dest="autoconnect",
        action="store_true",
        help="Automatically connect to first discovered server",
    )

    args = parser.parse_args()

    # Load JSONs
    cli_group_configs = {}
    cli_common_config = {}

    if args.devices:
        for file_path in args.devices:
            try:
                # Load config
                group_cfg = load_json_file(file_path)

                # Since a group name won't be provided by default, use
                # filename stem as base group id and ensure uniqueness
                stem = Path(file_path).stem or "group"
                group_id = stem
                suffix = 1

                while group_id in cli_group_configs:
                    group_id = f"{stem}_{suffix}"
                    suffix += 1

                # Ensure that group_cfg is dict_of_dicts
                if not is_dict_of_dicts(group_cfg):
                    raise ValueError(
                        "Specified device group configuration must be a dict of dict"
                    )

                cli_group_configs[group_id] = group_cfg

            except ValueError as e:
                print(f"Invalid device group configuration in {file_path}: {e}")

    if args.common:
        try:
            cli_common_config = load_json_file(args.common)
        except Exception as e:
            print(f"Invalid common configuration in {args.common}: {e}")

    qdarktheme.enable_hi_dpi()
    app = QApplication(sys.argv)
    qdarktheme.setup_theme(theme="auto")

    # Create and show main window with parsed configs and flags
    # NOTE: Every config passed is JSON/dict format.
    window = BioViewMonitor(
        group_configs=cli_group_configs,
        common_config=cli_common_config,
        autodiscover=args.autodiscover,
        autoconnect=args.autoconnect,
    )
    window.show()

    # If auto-discover, trigger the call
    if window.autodiscover and window.client_worker:
        handler = window.client_worker
        handler.discover_servers()

        if window.autoconnect and len(handler.discovered_servers) > 0:
            handler.change_selected_server(0)
            handler.connect_to_server()

    sys.exit(app.exec())

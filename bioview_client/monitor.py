"""
BioView Monitor can be launched via CLI, with/without configuration JSONs pre-specified. They may also be launched using GUI without any specified configuration.
In case no valid configuration files are found, the app will prompt the user to provide configuration JSONs.
Regardless on any configurations, the UI will load with appropriate components/default values
"""
import argparse
import contextlib
import logging  # TODO: Remove
import queue
import sys
from pathlib import Path
from typing import Dict, List

from bioview_common import Configuration, DataSource, DeviceStatus
from bioview_common.protocol.status import ClientStatus
from PyQt6.QtCore import QRunnable, QThreadPool, pyqtSlot
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
        If True, the application will automatically scan for available servers on startup.
        Default is True.
    autoconnect: bool
        If True, the application will attempt to connect to the first discovered server
        after autodiscovery. Default is False.
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
        # Return in a standardized format
        common_cfg, group_cfgs = self._resolve_initial_configs(
            common_config, group_configs
        )
        self.common_config = self._convert_dict_to_configuration(common_cfg)
        self.group_configs = self._convert_group_configs(group_cfgs)

        # Store device names and states - since that's all the UI needs
        # Use group->device mapping in self.device_states
        self.device_states: Dict[str, Dict] = {}

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
        self.status_bar = StatusBar(self)
        self.setStatusBar(self.status_bar)

    # --- Configuration helpers (extracted to reduce __init__ complexity) ---
    def _resolve_initial_configs(self, common_config, group_configs):
        """Return tuple (common_cfg, device_cfgs) after possibly prompting user."""
        if not common_config or not group_configs:
            dialog = ConfigurationPrompt()
            configurations = None

            if dialog.exec() == QDialog.DialogCode.Accepted:
                configurations = dialog.get_configurations()

            if configurations:
                common_cfg = configurations.get("common", None)
                group_cfgs = configurations.get("groups", None)
            else:
                common_cfg = DEFAULT_COMMON_CONFIGURATION
                group_cfgs = {}
        else:
            common_cfg = common_config
            group_cfgs = group_configs

        return common_cfg, group_cfgs

    def _convert_dict_to_configuration(self, common_cfg: Dict | Configuration):
        """Convert common config dict into a Configuration when possible."""
        if isinstance(common_cfg, Configuration):
            return common_cfg
        if isinstance(common_cfg, dict):
            try:
                return Configuration.from_dict(common_cfg)
            except Exception:
                return common_cfg
        return common_cfg

    def _convert_group_configs(self, group_cfgs: Dict):
        if not is_dict_of_dicts(group_cfgs):
            return {}

        converted: Dict[str, Dict] = {}
        for group_id, device_dict in group_cfgs.items():
            converted[group_id] = {}
            for device_id, device_cfg in device_dict.items():
                converted[group_id][device_id] = self._convert_dict_to_configuration(
                    device_cfg
                )

        return converted

    def _setup_client(self):
        """Connect to client and wire UI handlers."""
        self._create_client_worker()
        self._connect_client_signals()
        self.client_worker.start_client()

    def _create_client_worker(self):
        """Create the Client worker instance."""
        self.client_worker = Client(
            common_config=self.common_config, group_configs=self.group_configs
        )

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
        self.client_worker.device_connected.connect(self.on_device_connected)
        self.client_worker.device_disconnected.connect(self.on_device_disconnected)
        # Notify UI when a device connection attempt fails
        if hasattr(self.client_worker, "device_connection_failed"):
            self.client_worker.device_connection_failed.connect(
                self.on_device_connection_failed
            )
        self.client_worker.streaming_started.connect(self.on_streaming_started)
        self.client_worker.streaming_stopped.connect(self.on_streaming_stopped)

        # Devices discovered -> update status bar device panel
        self.client_worker.devices_discovered.connect(self._handle_devices_discovered)

        # General info signals
        self.client_worker.error_occurred.connect(
            lambda msg: self.log_display_panel.log_message("error", msg)
        )
        self.client_worker.log_message.connect(self.log_display_panel.log_message)

    # removed temporary stdout print hook

    # Legacy device pre-population helpers removed. The UI will only be
    # updated from discovery responses to prevent showing stale configurations.

    def _connect_signals(self):
        """
        Connect signals from all UI components to respective calls in client worker
        """
        if self.client_worker is None:
            self._setup_client()

        # Defer to smaller helpers to reduce cognitive complexity for linters
        self._connect_command_bar_signals()
        self._connect_settings_panel_signals()
        self._connect_plot_grid_signals()
        self._connect_statusbar_signals()

    def _handle_server_connection_request(self, server_info: dict):
        """Handle server connect requests from the UI"""
        if not server_info:
            return
        # Set selected server on client worker and ask it to connect
        if self.client_worker:
            self.client_worker.selected_server = server_info

            # Update UI immediately to show connecting state: enable spinner visually
            # No UI spinner in ServerConnector; proceed to connect

            # Run the actual connect in background so UI remains responsive
            class _ConnectTask(QRunnable):
                def __init__(self, client_ref):
                    super().__init__()
                    self.client_ref = client_ref

                @pyqtSlot()
                def run(self):
                    self.client_ref.connect_to_server()

            try:
                task = _ConnectTask(self.client_worker)
                # use client's thread pool if available
                pool = getattr(self.client_worker, "thread_pool", None)
                if pool is not None:
                    pool.start(task)
                else:
                    # fallback to creating a small pool
                    qp = QThreadPool()
                    qp.start(task)
            except Exception:
                # best-effort: fallback to synchronous call
                with contextlib.suppress(Exception):
                    self.client_worker.connect_to_server()

        # Listen for final server_connected/server_disconnected to hide spinner
        try:
            self.client_worker.server_connected.connect(
                lambda ok: self.status_bar.server_connector._server_spinner.setVisible(
                    False
                )
            )
            self.client_worker.server_disconnected.connect(
                lambda ok: self.status_bar.server_connector._server_spinner.setVisible(
                    False
                )
            )
        except Exception:
            pass

    # --- Extracted signal connection helpers (keeps _connect_signals small) ---
    def _connect_command_bar_signals(self):
        self.command_bar.connect_devices.connect(self.on_device_connection_requested)
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

        # logging hookup
        self.settings_panel.log_event.connect(self.log_display_panel.log_message)

    def _connect_plot_grid_signals(self):
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
            self.client_worker.discover_devices
        )

    def _handle_devices_discovered(self, devices_map: dict):
        """Update status bar device panel when client reports discovered devices."""
        if not devices_map:
            return

        # Enforce strict group->device mapping for the status bar. Reject other shapes.
        if not is_dict_of_dicts(devices_map):
            self.log_display_panel.log_message(
                "warning",
                "Received devices payload with invalid shape; expected dict-of-dicts, ignoring",
            )
            return

        try:
            # device_states is expected to be group->device->{config,state}
            self.device_states = devices_map
            self._trace(
                "debug", f"Devices discovered: groups={list(devices_map.keys())}"
            )
            if getattr(self.status_bar, "update_devices", None):
                # Forward discovery payload to status bar
                self.status_bar.update_devices(devices_map)
                self._trace("debug", "StatusBar.update_devices called")
        except Exception as e:
            self.log_display_panel.log_message("error", f"Failed to update devices: {e}")

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
    def on_device_connection_requested(self):
        if self.client_worker:
            # Request server to connect all initialized devices
            # Update UI to show CONNECTING state for all known devices
            for group in self.device_states.values():
                for device_id in group:
                    self.status_bar.update_device_state(
                        device_id, DeviceStatus.CONNECTING
                    )
            # Request device connection; UI will be updated via device_connected signals
            self.client_worker.connect_device()

    def update_save_state(self):
        self.saving_status = True
        if self.client_worker:
            pass

    def toggle_instructions(self, flag):
        self.enable_instructions = flag
        if self.instruction_dialog is not None:
            self.instruction_dialog.toggle_ui(self.enable_instructions)

    # Client worker helper functions
    def on_server_connected(self, connected=True):
        """Handle server connection"""
        # connected is a boolean emitted by client_worker.server_connected
        if connected:
            # Centralize UI updates via ServerStatus
            self.log_display_panel.log_message("info", "Connected to server")

            try:
                self.status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)
            except Exception:
                self.log_display_panel.log_message(
                    "warning", "Unable to update status bar"
                )
                self.status_bar.set_server_status(ClientStatus.SERVER_CONNECTED)

            # Auto-ping
            if self.client_worker:
                self.client_worker.ping_server()

            # Ensure UI buttons reflect connected state (defensive)
            with contextlib.suppress(Exception):
                # still ensure discover button enabled
                self.status_bar.server_connector.discover_btn.setEnabled(True)

        else:
            try:
                self.status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
            except Exception:
                self.log_display_panel.log_message(
                    "warning", "Unable to update status bar"
                )

            self.log_display_panel.log_message("warning", "Failed to connect to server")

        # TODO: Populate display sources by querying server

    def on_server_disconnected(self):
        """Handle server disconnection"""
        try:
            self.status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
        except Exception:
            self.log_display_panel.log_message("warning", "Unable to update status bar")
        self.log_display_panel.log_message("warning", "Disconnected from server")

    def on_device_connected(self, device_id, success=True):
        self._trace(
            "debug",
            f"on_device_connected called with device_id={device_id} success={success}",
        )
        if device_id is None:
            # All devices connected
            for group_id, group in self.device_states.items():
                for device_id in group:
                    new_state = (
                        DeviceStatus.CONNECTED if success else DeviceStatus.DISCONNECTED
                    )
                    self.device_states[group_id][device_id]["state"] = new_state
                    self.status_bar.update_device_state(device_id, new_state)
        else:
            # Single device connected (device_id is the canonical id)
            # Update device_states mapping when possible
            for group_id, group in self.device_states.items():
                if device_id in group:
                    self.device_states[group_id][device_id]["state"] = (
                        DeviceStatus.CONNECTED if success else DeviceStatus.DISCONNECTED
                    )
                    break
            # Update status bar regardless
            self.status_bar.update_device_state(
                device_id,
                DeviceStatus.CONNECTED if success else DeviceStatus.DISCONNECTED,
            )
            self._trace("info", f"Device connected: {device_id}")

        # Check if all are connected and if so, disable UI buttons
        self.update_buttons()

    def on_device_disconnected(self, device_id=None, success=True):
        self._trace(
            "debug",
            f"on_device_disconnected called device_id={device_id} success={success}",
        )
        if device_id is None:
            # Disconnect devices (all)
            for group_id, group in self.device_states.items():
                for device_id in group:
                    self.device_states[group_id][device_id][
                        "state"
                    ] = DeviceStatus.DISCONNECTED
                    self.status_bar.update_device_state(
                        device_id, DeviceStatus.DISCONNECTED
                    )
        else:
            for group_id, group in self.device_states.items():
                if device_id in group:
                    self.device_states[group_id][device_id][
                        "state"
                    ] = DeviceStatus.DISCONNECTED
                    break
            with contextlib.suppress(Exception):
                self.status_bar.update_device_state(device_id, DeviceStatus.DISCONNECTED)

        self.update_buttons()

    def on_device_connection_failed(self, device_id: str):
        """Handle a failed device connection attempt by updating UI state."""
        if device_id is None:
            # If None, assume all failed
            for group_id, group in self.device_states.items():
                for did in group:
                    self.device_states[group_id][did][
                        "state"
                    ] = DeviceStatus.DISCONNECTED
                    self.status_bar.update_device_state(did, DeviceStatus.DISCONNECTED)
        else:
            for group_id, group in self.device_states.items():
                if device_id in group:
                    self.device_states[group_id][device_id][
                        "state"
                    ] = DeviceStatus.DISCONNECTED
                    break
            # Update status bar regardless
            with contextlib.suppress(Exception):
                self.status_bar.update_device_state(device_id, DeviceStatus.DISCONNECTED)

    def on_streaming_started(self):
        pass

    def on_streaming_stopped(self):
        pass

    def update_buttons(self):
        # Consider all devices connected only if none are DISCONNECTED
        connected = True
        for group in self.device_states.values():
            for device_dict in group.values():
                if device_dict.get("state") == DeviceStatus.DISCONNECTED:
                    connected = False
                    break
            if not connected:
                break

        if connected:
            self.command_bar.update_button_states(DeviceStatus.CONNECTED)
        else:
            self.command_bar.update_button_states(DeviceStatus.DISCONNECTED)


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
                # Since a group name won't be provided by default, use filename stem
                # as base group id and ensure uniqueness
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
    window = BioViewMonitor(
        group_configs=cli_group_configs,
        common_config=cli_common_config,
        autodiscover=args.autodiscover,
        autoconnect=args.autoconnect,
    )
    window.show()

    # Optionally trigger autodiscover. If autoconnect is requested, only attempt
    # connection after the scan completes (one-time handler).
    if window.autodiscover and window.client_worker:
        try:
            if window.autoconnect:

                def _on_scan_complete(servers):
                    try:
                        # servers is a list of discovered server info dicts. If any found,
                        # pick the first and set it as the selected server so
                        # connect_to_server() will not re-run discovery.
                        if servers and isinstance(servers, list) and len(servers) > 0:
                            with contextlib.suppress(Exception):
                                window.client_worker.selected_server = servers[0]

                        # attempt connect once (will skip discovery if selected_server set)
                        with contextlib.suppress(Exception):
                            window.client_worker.connect_to_server()

                    finally:
                        # disconnect this handler to avoid repeated attempts
                        with contextlib.suppress(Exception):
                            window.client_worker.server_scan_completed.disconnect(
                                _on_scan_complete
                            )

                window.client_worker.server_scan_completed.connect(_on_scan_complete)

            window.client_worker.discover_servers()
        except Exception:
            pass
    elif window.autoconnect and window.client_worker:
        # If autodiscover is disabled but autoconnect requested, attempt connect
        # directly (this will perform an internal discover if needed).
        with contextlib.suppress(Exception):
            window.client_worker.connect_to_server()

    sys.exit(app.exec())

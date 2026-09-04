"""BioView Monitor: the live acquisition window.

Runs with or without a configuration file; missing configuration is prompted
for at startup. See bioview-docs/architecture/client.md.
"""
import argparse
import contextlib
import logging  # TODO: Remove
import sys
import time
from pathlib import Path

from bioview_common import (
    SUPPORTED_CONFIGURATION_TYPES,
    ClientStatus,
    DataSource,
    DeviceStatus,
    ExperimentConfiguration,
    parse_configuration_file,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from bioview_client.assets import APP_DESKTOP_NAME, get_app_icon
from bioview_client.autoconnect import start_localhost_autoconnect
from bioview_client.components import (
    AnnotateEventPanel,
    AppControlPanel,
    ConfigurationPrompt,
    InstructionController,
    LogDisplayPanel,
    PlotGrid,
    SettingsPanel,
    StatusBar,
    parse_timed_modes,
)
from bioview_client.components.common import Toast
from bioview_client.handler import Client


def split_configurations(configurations):
    """Split a parsed config into (all configs, experiment config, device groups).

    A missing EXPERIMENT block is defaulted *into the returned mapping*, which
    is what SettingsPanel builds the plot-source selector from.
    """
    configurations = dict(configurations)

    experiment_cfg_id = None
    for cfg_id, cfg in configurations.items():
        if cfg.get_type() == SUPPORTED_CONFIGURATION_TYPES.EXPERIMENT:
            experiment_cfg_id = cfg_id
            break

    if experiment_cfg_id is None:
        experiment_cfg_id = "Experiment"
        configurations[experiment_cfg_id] = ExperimentConfiguration({})

    experiment_config = configurations[experiment_cfg_id]
    group_configs = {k: v for k, v in configurations.items() if k != experiment_cfg_id}
    return configurations, experiment_config, group_configs


class BioViewMonitor(QMainWindow):
    """The main acquisition window.

    Missing ``group_configs``/``experiment_config`` are prompted for via a dialog.
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
        self.autodiscover = autodiscover
        self.autoconnect = autoconnect

        self.config_file = config_file
        if isinstance(self.config_file, list | tuple):
            self.config_file = self.config_file[0] if self.config_file else None

        if not self.config_file:
            dialog = ConfigurationPrompt()

            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.config_file = dialog.get_config_file()

        self.configurations = parse_configuration_file(self.config_file)

        (
            self.configurations,
            self.experiment_config,
            self.group_configs,
        ) = split_configurations(self.configurations)

        self.device_status = {k: DeviceStatus.NOINIT for k in self.group_configs}

        self.saving_status = False

        # Routines pair a fixed duration with optional instructions; the
        # free-running "unlimited" mode is always available alongside them.
        self.timed_modes = parse_timed_modes(
            self.experiment_config.get_timed_modes(),
            base_dir=self._config_base_dir(),
        )
        self.active_timed_mode = None
        self.instruction_controller = None
        self._routine_deadline = 0.0

        # Drives the bottom progress bar + auto-stop for a running timed mode
        self.routine_timer = QTimer(self)
        self.routine_timer.setInterval(200)
        self.routine_timer.timeout.connect(self._on_routine_tick)

        self._init_ui()

        self.client_worker = Client(
            experiment_config=self.experiment_config, group_configs=self.group_configs
        )
        self._connect_client_signals()
        self.client_worker.start_client()
        self.command_bar.update_button_states(self.client_worker.status)

        self._connect_signals()

        self.available_sources = []

    def _init_ui(self):
        self.setWindowTitle("BioView Data Monitor")
        # Per-window icon; GNOME and macOS ignore it and use the app icon
        # set in run_monitor() instead.
        self.setWindowIcon(get_app_icon())
        screen = QGuiApplication.primaryScreen().geometry()
        width = screen.width()
        height = screen.height()
        self.setGeometry(
            int(0.2 * width), int(0.1 * height), int(0.6 * width), int(0.8 * height)
        )

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        from PyQt6.QtWidgets import QSplitter

        splitter = QSplitter(Qt.Orientation.Vertical)

        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        controls_layout = QVBoxLayout()

        self.command_bar = AppControlPanel()
        self.command_bar.set_routines([m.label for m in self.timed_modes])
        controls_layout.addWidget(self.command_bar, stretch=1)

        self.settings_panel = SettingsPanel(self.configurations)

        controls_layout.addWidget(self.settings_panel, stretch=3)

        top_layout.addLayout(controls_layout, stretch=3)

        self.meta_panels = QVBoxLayout()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.log_display_panel = LogDisplayPanel(logger=self.logger)
        self.meta_panels.addWidget(self.log_display_panel, stretch=3)

        # Annotations live in the recording's .bvr file, so the panel only
        # emits text and the monitor routes it to the client.
        self.annotate_event_panel = AnnotateEventPanel()
        self.meta_panels.addWidget(self.annotate_event_panel, stretch=2)
        top_layout.addLayout(self.meta_panels, stretch=2)

        self.plot_grid = PlotGrid(self.experiment_config)

        # Enforce plot heights (50% to 60% of the initial window height)
        window_height = int(0.8 * height)
        self.plot_grid.setMinimumHeight(int(0.5 * window_height))
        self.plot_grid.setMaximumHeight(int(0.6 * window_height))

        splitter.addWidget(top_widget)
        splitter.addWidget(self.plot_grid)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)
        central_widget.setLayout(main_layout)

        self.status_bar = StatusBar(device_status=self.device_status, parent=self)
        self.setStatusBar(self.status_bar)

    def _connect_client_signals(self):
        """Connect client signals to UI handlers."""
        self.client_worker.server_scan_completed.connect(
            self.status_bar.on_scan_complete
        )
        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)

        self.client_worker.server_scan_progress.connect(
            self.status_bar.update_scan_progress
        )

        self.client_worker.device_init_succeeded.connect(
            self.update_status_bar_and_buttons
        )
        self.client_worker.device_init_failed.connect(self.on_device_init_failed)
        self.client_worker.device_init_succeeded.connect(self.on_devices_ready)
        self.client_worker.devices_discovered.connect(self.on_devices_ready)
        self.client_worker.device_disconnect_succeeded.connect(
            self.update_status_bar_and_buttons
        )
        # A live parameter change can add or drop streams.
        self.client_worker.data_sources_changed.connect(self.populate_plot_grid_sources)
        self.client_worker.streaming_started.connect(
            lambda x: self._handle_streaming_status_changed(x)
        )
        self.client_worker.streaming_stopped.connect(
            lambda x: self._handle_streaming_status_changed(not x)
        )
        self.client_worker.devices_discovered.connect(self.update_status_bar_and_buttons)

        # Queued: data bursts are marshalled to the UI thread one at a time
        # and never re-enter the receiving path.
        self.client_worker.data_received.connect(
            self.on_data_received, Qt.ConnectionType.QueuedConnection
        )

        self.client_worker.log_message.connect(self.log_display_panel.log_message)

    def _connect_signals(self):
        """Wire UI component signals to the client worker."""
        self._connect_command_bar_signals()
        self._connect_settings_panel_signals()
        self._connect_statusbar_signals()
        self._connect_annotation_signals()

    def _connect_annotation_signals(self):
        """Wire the Mark Events panel to the client so annotations are stored in
        the active recording, and surface its log events in the experiment log."""
        self.annotate_event_panel.annotation_requested.connect(
            self.on_annotation_requested
        )
        self.annotate_event_panel.log_event.connect(self.log_display_panel.log_message)

    def _connect_command_bar_signals(self):
        self.command_bar.initialize_devices.connect(self.on_device_init_requested)
        # Stop is routed through a handler that also tears down a running routine.
        self.command_bar.start_streaming.connect(self.handle_start_streaming)
        self.command_bar.stop_streaming.connect(self.handle_stop_streaming)
        self.command_bar.enable_data_saving.connect(self.update_save_state)
        self.command_bar.routine_selected.connect(self.on_routine_selected)

    def _connect_settings_panel_signals(self):
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

        if getattr(self.settings_panel, "device_param_changed", None):
            self.settings_panel.device_param_changed.connect(
                self.on_device_param_changed
            )

        if getattr(self.settings_panel, "run_dpic_balance", None):
            self.settings_panel.run_dpic_balance.connect(
                self.client_worker.run_dpic_balance
            )

        self.settings_panel.log_event.connect(self.log_display_panel.log_message)
        self.plot_grid.log_event.connect(self.log_display_panel.log_message)

    def _connect_statusbar_signals(self):
        self.status_bar.network_scan_requested.connect(
            self.client_worker.discover_servers
        )

        self.status_bar.network_scan_cancel_requested.connect(
            self.client_worker.cancel_scan
        )

        self.status_bar.selected_server_changed.connect(
            self.client_worker.change_selected_server
        )

        self.status_bar.server_connection_requested.connect(
            self.client_worker.connect_to_server
        )

        self.status_bar.server_disconnection_requested.connect(
            self.client_worker.disconnect_from_server
        )

        self.status_bar.discover_devices_requested.connect(
            lambda: self.client_worker.initialize_devices(True)
        )

    def _handle_streaming_status_changed(self, is_streaming: bool):
        if hasattr(self.settings_panel, "set_streaming_locked"):
            self.settings_panel.set_streaming_locked(is_streaming)
        if is_streaming:
            self.on_streaming_started()
        # Never promote a failed device to connected.
        for group_id, current in list(self.device_status.items()):
            if group_id == "metadata":
                continue

            if is_streaming:
                if current == DeviceStatus.CONNECTED:
                    self.device_status[group_id] = DeviceStatus.STREAMING
                    self.status_bar.update_device_status(
                        group_id, DeviceStatus.STREAMING
                    )
            else:
                if current == DeviceStatus.STREAMING:
                    self.device_status[group_id] = DeviceStatus.CONNECTED
                    self.status_bar.update_device_status(
                        group_id, DeviceStatus.CONNECTED
                    )

        # A routine cannot outlive the stream it was recording.
        if not is_streaming and self.active_timed_mode is not None:
            self._cleanup_timed_mode()

        client_status = self.client_worker.status
        self.command_bar.update_button_states(client_status)

    def keyPressEvent(self, event):
        """F11 toggles true fullscreen; Esc only leaves it (back to maximized)."""
        key = event.key()
        if key == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showMaximized()
            else:
                self.showFullScreen()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showMaximized()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        """Handle application close"""
        self._stop_instruction()
        self.routine_timer.stop()
        if self.client_worker:
            self.client_worker.stop_client()
        event.accept()

    # Handlers for UI updates
    def handle_time_window_change(self, seconds):
        self.plot_grid.set_display_time(seconds)

    def handle_grid_layout_change(self, rows, cols):
        # Sources that no longer fit the smaller grid come back here so their
        # ticks can be cleared.
        dropped = self.plot_grid.update_grid(rows, cols)
        for src in dropped or []:
            self.settings_panel.update_source("remove", src)

    def populate_plot_grid_sources(self, sources):
        """Reconcile the plot-source selector with the server's advertised list.

        Accepts DataSource objects or descriptor dicts. Sources that have gone
        away are unplotted; surviving ones keep their tick.
        """
        if sources is None:
            return

        source_objs = []
        for src in sources:
            if isinstance(src, DataSource):
                source_objs.append(src)
            elif isinstance(src, dict):
                source_objs.append(DataSource.from_dict(src))

        self.available_sources = source_objs
        available = set(source_objs)

        # Free the grid cells held by sources the server no longer offers.
        for src in list(self.plot_grid.selected_channels.keys()):
            if src not in available:
                self.plot_grid.remove_source(src)

        # Rebuilding the model clears every tick; restore what is still plotted.
        still_plotted = list(self.plot_grid.selected_channels.keys())
        self.settings_panel.set_available_sources(source_objs)
        for src in still_plotted:
            self.settings_panel.update_source("add", src)

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
        """Connect a new data source to the plot grid."""
        if self.plot_grid.add_source(source):
            self.settings_panel.update_source("add", source)

    def remove_plot_source(self, source: DataSource):
        """Remove a data source from the plot grid."""
        if self.plot_grid.remove_source(source):
            self.settings_panel.update_source("remove", source)

    # Command Bar helper functions
    def on_device_init_requested(self):
        if not self.client_worker:
            return

        # device_status is a flat {group_id: DeviceStatus} mapping.
        for group_id in self.device_status:
            if group_id == "metadata":
                continue
            self.status_bar.update_device_status(group_id, DeviceStatus.CONNECTING)

        self.command_bar.initialize_button.setEnabled(False)

        self.client_worker.initialize_devices()

    def on_device_init_failed(self):
        """Reset UI when device initialization fails or times out."""
        for group_id in self.device_status:
            if group_id == "metadata":
                continue
            self.status_bar.update_device_status(group_id, DeviceStatus.DISCONNECTED)
        if self.client_worker:
            self.command_bar.update_button_states(self.client_worker.status)

    def _show_toast(self, message: str, level: str = "info"):
        """Show a transient toast notification overlaid on the main window."""
        with contextlib.suppress(Exception):
            Toast.show_message(self, message, level=level)

    def _warn_missing_save_target(self) -> bool:
        """If the user has not provided both a file name and a save folder, warn
        via a toast and return True (i.e. the save target is missing)."""
        if self.client_worker and self.client_worker.has_valid_save_target():
            return False
        self._show_toast(
            "Provide a file name and a save folder before saving or marking events.",
            level="warning",
        )
        self.log_display_panel.log_message(
            "warning",
            "Cannot save: a file name and a save folder are both required.",
        )
        return True

    def update_save_state(self, enabled: bool = True):
        # Saving needs both a file name and a folder; revert the checkbox if
        # either is missing.
        if enabled and self._warn_missing_save_target():
            with contextlib.suppress(Exception):
                self.command_bar.save_checkbox.setChecked(False)
            self.saving_status = False
            if self.client_worker:
                self.client_worker.set_save_enabled(False)
            return

        self.saving_status = bool(enabled)
        if self.client_worker:
            self.client_worker.set_save_enabled(bool(enabled))

    def on_annotation_requested(self, text: str):
        """Store a "Mark Event" annotation in the active recording."""
        if self._warn_missing_save_target():
            return

        if not self.client_worker or not self.client_worker.record_annotation(text):
            self._show_toast("Start a recording before marking events.", level="warning")
            self.log_display_panel.log_message(
                "warning", "No active recording to attach the annotation to."
            )
            return

        self.annotate_event_panel.clear_annotation()
        self._show_toast("Event marked.", level="success")
        self.log_display_panel.log_message("info", f"Marked event: {text}")

    # Timed-mode (routine) orchestration
    def _config_base_dir(self) -> Path:
        """Directory used to resolve relative instruction file paths."""
        cf = self.config_file
        if isinstance(cf, list | tuple):
            cf = cf[0] if cf else None
        if cf:
            with contextlib.suppress(Exception):
                return Path(cf).resolve().parent
        return Path.cwd()

    def on_routine_selected(self, index: int):
        if index < 0 or index >= len(self.timed_modes):
            return
        self.start_timed_mode(self.timed_modes[index])

    def start_timed_mode(self, mode):
        if self.client_worker.status < ClientStatus.DEVICES_CONNECTED:
            self.log_display_panel.log_message(
                "warning", "Connect and initialize devices before running a routine"
            )
            self.command_bar.reset_routine_selection()
            return
        if self.client_worker.status == ClientStatus.STREAMING:
            self.log_display_panel.log_message(
                "warning", "Already streaming; stop before starting a routine"
            )
            self.command_bar.reset_routine_selection()
            return

        if self._warn_missing_save_target():
            self.command_bar.reset_routine_selection()
            return

        self.active_timed_mode = mode

        # Timed modes always save, as <file name>_<routine label>.bvr.
        self.command_bar.save_checkbox.setChecked(True)
        self.update_save_state(True)
        self.client_worker.set_save_label(mode.label)

        self._start_instruction(mode.instruction)
        self.client_worker.start_streaming()

        self.status_bar.start_routine(mode.label, mode.duration)
        self._routine_deadline = time.monotonic() + mode.duration
        self.routine_timer.start()

        self.log_display_panel.log_message(
            "info", f"Started routine '{mode.label}' ({int(mode.duration)}s)"
        )

    def _on_routine_tick(self):
        if self.active_timed_mode is None:
            self.routine_timer.stop()
            return

        remaining = self._routine_deadline - time.monotonic()
        elapsed = self.active_timed_mode.duration - remaining
        self.status_bar.update_routine(elapsed, self.active_timed_mode.duration)

        if remaining <= 0:
            self.stop_timed_mode(completed=True)

    def _cleanup_timed_mode(self):
        """Tear down routine UI/instructions without touching the data stream."""
        self.routine_timer.stop()
        self._stop_instruction()
        self.status_bar.stop_routine()
        self.command_bar.reset_routine_selection()
        self.active_timed_mode = None

    def stop_timed_mode(self, completed: bool = False):
        if self.active_timed_mode is None:
            return
        label = self.active_timed_mode.label
        self._cleanup_timed_mode()
        self.client_worker.stop_streaming()
        if completed:
            self.log_display_panel.log_message("info", f"Routine '{label}' complete")

    def on_device_param_changed(self, device_id, param, value):
        """Forward UI tweaks to the client config snapshot and live backend."""
        if self.client_worker is not None:
            self.client_worker.record_param_change(device_id, param, value)

        if (
            self.client_worker is not None
            and self.client_worker.status >= ClientStatus.DEVICES_CONNECTED
        ):
            self.client_worker.configure_device(device_id, {param: value})

    def handle_start_streaming(self):
        """Start button (unlimited mode): this is not a timed routine, so clear any
        routine label before streaming. Recordings are then named <file name>.bvr."""
        if self.saving_status and self._warn_missing_save_target():
            return
        self.client_worker.set_save_label(None)
        self.client_worker.start_streaming()

    def handle_stop_streaming(self):
        """Stop button: cancel a running routine (which also stops the stream) or
        stop a plain unlimited-mode stream."""
        if self.active_timed_mode is not None:
            self.stop_timed_mode(completed=False)
        else:
            self.client_worker.stop_streaming()

    def _start_instruction(self, spec):
        self._stop_instruction()
        if spec is None:
            return
        self.instruction_controller = InstructionController(spec, host_widget=self)
        self.instruction_controller.log_event.connect(self.log_display_panel.log_message)
        self.instruction_controller.start()

    def _stop_instruction(self):
        if self.instruction_controller is not None:
            with contextlib.suppress(Exception):
                self.instruction_controller.stop()
            with contextlib.suppress(Exception):
                self.instruction_controller.deleteLater()
            self.instruction_controller = None

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

        data_sources = self.client_worker.get_data_sources()
        if data_sources:
            self.populate_plot_grid_sources(data_sources)

    def on_server_disconnected(self):
        try:
            self.status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
        except Exception:
            self.log_display_panel.log_message("warning", "Unable to update status bar")

        self.available_sources = []
        self.settings_panel.set_available_sources([])
        self.plot_grid.clear_sources()

        self.command_bar.update_button_states(self.client_worker.status)
        self.log_display_panel.log_message("warning", "Disconnected from server")

    def on_streaming_started(self):
        if hasattr(self.settings_panel, "set_streaming_locked"):
            self.settings_panel.set_streaming_locked(True)

    def update_status_bar_and_buttons(self, device_status: dict):
        for group_id, new_status in device_status.items():
            if group_id == "metadata":
                continue

            if not isinstance(new_status, DeviceStatus):
                with contextlib.suppress(Exception):
                    new_status = DeviceStatus(new_status)
            self.device_status[group_id] = new_status
            self.status_bar.update_device_status(group_id, new_status)

        client_status = self.client_worker.status
        self.command_bar.update_button_states(client_status)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch BioView Monitor UI")
    parser.add_argument(
        "--config-file",
        nargs="*",
        help="In case the app is launched using a .bvi file",  # A .json also works.
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
    return parser


def run_monitor(argv=None) -> int:
    """Build the Qt application, show the window and run the event loop.

    Returns the Qt exit code rather than calling ``sys.exit``, so the caller
    can clean up afterwards.
    """
    import qdarktheme  # Provide consistent styling across all OSes

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    qdarktheme.enable_hi_dpi()
    app = QApplication(sys.argv)
    # App-wide branding: taskbar/dock icon plus the .desktop association
    # Wayland and KDE use to pick the launcher icon.
    app.setApplicationName("BioView")
    app.setApplicationDisplayName("BioView Data Monitor")
    app.setDesktopFileName(APP_DESKTOP_NAME)
    app.setWindowIcon(get_app_icon())
    qdarktheme.setup_theme(theme="dark")

    window = BioViewMonitor(
        config_file=args.config_file,
        autodiscover=args.autodiscover,
        autoconnect=args.autoconnect,
    )
    # Maximized, not fullscreen: the title bar and taskbar stay visible.
    window.showMaximized()

    # Probe loopback and latch on as soon as a local server answers, so the
    # window can be started before its server.
    window._localhost_timer = start_localhost_autoconnect(window.client_worker, window)

    # The scan is asynchronous, so autoconnect waits for it to complete.
    if window.autodiscover and window.client_worker:
        handler = window.client_worker

        if window.autoconnect:

            def _autoconnect_when_scan_done(servers):
                # Stay subscribed when a scan finds nothing, so a later retry
                # still autoconnects.
                if handler.status >= ClientStatus.SERVER_CONNECTED:
                    with contextlib.suppress(Exception):
                        handler.server_scan_completed.disconnect(
                            _autoconnect_when_scan_done
                        )
                    return
                if servers:
                    with contextlib.suppress(Exception):
                        handler.server_scan_completed.disconnect(
                            _autoconnect_when_scan_done
                        )
                    handler.change_selected_server(0)
                    handler.connect_to_server()

            handler.server_scan_completed.connect(_autoconnect_when_scan_done)

        # Re-scan until connected, so the window can outlive a missing server.
        rescan_timer = QTimer()

        def _maybe_rescan():
            # A scan already in flight is left alone.
            if handler.status >= ClientStatus.SERVER_CONNECTED:
                rescan_timer.stop()
                return
            if handler.status == ClientStatus.SCANNING:
                return
            # Keep retrying only in autoconnect mode; otherwise let the user act.
            if handler.discovered_servers and not window.autoconnect:
                rescan_timer.stop()
                return
            handler.discover_servers()

        rescan_timer.timeout.connect(_maybe_rescan)
        rescan_timer.start(5000)
        window._rescan_timer = rescan_timer

        handler.discover_servers()

    return app.exec()


if __name__ == "__main__":
    # Routed through the launcher so a directly-run window still gets
    # (and is counted against) the shared localhost server.
    from bioview_client.launch import main as _launch

    sys.exit(_launch(["--role", "monitor", *sys.argv[1:]]))

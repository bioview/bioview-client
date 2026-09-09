"""BioView Monitor: the live acquisition window.

Runs with or without a configuration file; missing configuration is prompted
for at startup. See bioview-docs/architecture/client.md.
"""

import argparse
import contextlib
import logging  # TODO: Remove
import math
import sys
import time
from pathlib import Path

from bioview_common import (
    CONTROL_PORT,
    DATA_PORT,
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
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from bioview_client import launch
from bioview_client.assets import APP_DESKTOP_NAME, get_app_icon
from bioview_client.autoconnect import start_localhost_autoconnect
from bioview_client.components import (
    AnnotateEventPanel,
    AppControlPanel,
    ConfigurationPrompt,
    DiagnosticsReporter,
    InstructionController,
    LogDisplayPanel,
    LogWindow,
    PlotGrid,
    ServerLostDialog,
    SettingsPanel,
    StatusBar,
    list_audio_outputs,
    parse_timed_modes,
)
from bioview_client.components.common import Toast
from bioview_client.handler import Client


def _normalize_source_name(name) -> str:
    """Fold a source name to a form a config file can be written against.

    Case, the group separator and runs of whitespace all vary between how a
    source is advertised and how someone types it into a configuration.
    """
    return " ".join(str(name).replace(":", " ").split()).casefold()


def _source_aliases(source: DataSource) -> set[str]:
    """The names a ``display_sources`` entry may legitimately use for a source.

    Sources are advertised as "<group>: <label>", but a configuration is
    written before the groups are known, so the bare channel label names the
    source just as well.
    """
    return {
        _normalize_source_name(source.get_display_label()),
        _normalize_source_name(source.label),
    }


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


def _is_local_server(server: dict) -> bool:
    """True for a server on this machine, which is the only one we may restart."""
    address = (server or {}).get("ip") or ""
    return address in ("127.0.0.1", "localhost", "::1")


class BioViewMonitor(QMainWindow):
    """The main acquisition window.

    Missing ``group_configs``/``experiment_config`` are prompted for via a dialog.
    """

    #: How long each open-ended operation runs before the status bar stops
    #: saying "working" and starts saying "still working". One number per
    #: operation, because what counts as slow differs by an order of
    #: magnitude: a handshake is a second, a USRP initialization is a minute.
    CONNECT_SLOW_MS = 6000
    SCAN_SLOW_MS = 8000
    DISCOVER_SLOW_MS = 15000
    INIT_SLOW_MS = 30000
    BALANCE_SLOW_MS = 20000

    def __init__(
        self,
        config_file: str | Path = None,
        # group_configs: List[Dict] = None,
        # experiment_config: Dict = None,
        autodiscover: bool = True,
        autoconnect: bool = False,
        control_port: int = CONTROL_PORT,
        data_port: int = DATA_PORT,
    ):
        super().__init__()
        self.autodiscover = autodiscover
        self.autoconnect = autoconnect
        self.control_port = control_port
        self.data_port = data_port

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

        # Groups that failed the most recent initialization, so the success
        # handler does not report an unqualified success over a partial one.
        self._pending_init_failures = {}

        # Server-level faults, shown once each. Scoped to the device types this
        # session actually uses: a BIOPAC driver that will not load is not this
        # operator's problem if the configuration has no BIOPAC in it.
        self._diagnostics = DiagnosticsReporter(self)
        self._configured_device_types = {
            str(cfg.get_param("device_type", "")).lower()
            for cfg in self.group_configs.values()
        }

        # What the status bar has been told about each group, so a repeated
        # status from the poll is not announced twice.
        self._announced_group_status = {}

        # True while a scan the user pressed for is outstanding; see
        # on_server_scan_completed.
        self._scan_requested = False

        # Sources the configuration asks to plot as soon as they are
        # discovered, and the names already honoured. Ticking is a one-shot
        # per name: the config states the starting view, it does not keep
        # re-checking a box the user has deliberately cleared.
        self._default_source_names = {
            _normalize_source_name(name)
            for name in (self.experiment_config.get_param("display_sources", []) or [])
        }
        self._applied_default_sources = set()

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
        self._check_routine_instructions()

        self.client_worker = Client(
            experiment_config=self.experiment_config,
            group_configs=self.group_configs,
            control_port=self.control_port,
            data_port=self.data_port,
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

        # Controls stack vertically: one button-high row of panels, then the
        # settings tabs across the full width. Settings need width far more
        # than the action row does, and a full-width tab page keeps its own
        # height down.
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(4)

        self.command_bar = AppControlPanel()
        self.command_bar.set_routines([m.label for m in self.timed_modes])
        # 40/60 between the action buttons and Mark Events: the buttons are
        # fixed-width, while the annotation box is a free-text field that gets
        # used mid-recording and benefits from every pixel it can have.
        action_row.addWidget(self.command_bar, stretch=2)

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        # The log lives in its own window, opened from the Control panel, so
        # the monitor's limited width goes to plots and settings instead.
        self.log_display_panel = LogDisplayPanel(logger=self.logger)
        self.log_window = LogWindow(self.log_display_panel, parent=self)
        self.command_bar.show_log.connect(self._toggle_log_window)
        self.log_display_panel.message_logged.connect(self._note_log_message)

        # Annotations live in the recording's .bvr file, so the panel only
        # emits text and the monitor routes it to the client.
        self.annotate_event_panel = AnnotateEventPanel()
        action_row.addWidget(self.annotate_event_panel, stretch=3)

        # Both panels are pinned to the taller of the two natural heights, so
        # the row is exactly one control tall and the two group boxes align.
        action_height = max(
            self.command_bar.sizeHint().height(),
            self.annotate_event_panel.sizeHint().height(),
        )
        self.command_bar.setFixedHeight(action_height)
        self.annotate_event_panel.setFixedHeight(action_height)
        top_layout.addLayout(action_row)

        self.settings_panel = SettingsPanel(self.configurations)
        top_layout.addWidget(self.settings_panel, stretch=1)

        self.plot_grid = PlotGrid(self.experiment_config)

        splitter.addWidget(top_widget)
        splitter.addWidget(self.plot_grid)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)
        central_widget.setLayout(main_layout)

        self._splitter = splitter
        self._controls_widget = top_widget
        self._action_height = action_height + top_layout.spacing()
        self._apply_vertical_budget(int(0.8 * height))

        # The grid has to be big enough for what the configuration asked to
        # plot before any source arrives; see _size_grid_for_defaults.
        self._size_grid_for_defaults()

        self.status_bar = StatusBar(device_status=self.device_status, parent=self)
        self.setStatusBar(self.status_bar)

    # Vertical budget: 65% of the window to plots, 30% to controls, the
    # remainder to the status bar. The settings panel used to spend ~36 px of
    # the controls budget on a tab bar and the padding around it; the panels
    # sit side by side now, so that height goes to the plots instead.
    PLOTS_SHARE = 0.65
    CONTROLS_SHARE = 0.30

    def _apply_vertical_budget(self, window_height: int):
        """Split the window height between controls and plots.

        Driven off the window's own height rather than the screen's, so the
        proportions survive a monitor with a different aspect ratio, a restored
        window size, or a display-scale change.
        """
        controls_height = int(self.CONTROLS_SHARE * window_height)
        plots_height = int(self.PLOTS_SHARE * window_height)

        # The settings strip gets whatever the action row leaves of the
        # controls budget; the floor keeps at least one row of settings
        # readable when the window is very short.
        self.settings_panel.setMaximumHeight(
            max(120, controls_height - self._action_height)
        )
        self._controls_widget.setMaximumHeight(controls_height)
        self.plot_grid.setMinimumHeight(int(0.45 * window_height))
        self._splitter.setSizes([controls_height, plots_height])

    #: Spin-box limits in the settings panel; the grid cannot exceed them or
    #: the displayed layout would not match the one in use.
    MAX_GRID_ROWS = 4
    MAX_GRID_COLS = 3

    @classmethod
    def _grid_for(cls, count: int) -> tuple[int, int]:
        """Smallest near-square layout holding ``count`` plots, 2x2 at minimum.

        Columns are filled before rows: a plot is a time series, so width buys
        more than height does.
        """
        if count <= 4:
            return 2, 2
        cols = min(cls.MAX_GRID_COLS, math.ceil(math.sqrt(count)))
        rows = min(cls.MAX_GRID_ROWS, math.ceil(count / cols))
        return rows, cols

    def _size_grid_for_defaults(self):
        """Grow the plot grid to fit every configured ``display_sources`` entry.

        The grid defaults to 2x2 and ``add_source`` refuses once the cells run
        out, so a configuration naming five sources silently lost its fifth --
        and lost it permanently, because a default is only ever applied once
        (see ``_apply_default_sources``). The count is known at startup, so the
        grid is sized for it before the first source is advertised.
        """
        wanted = len(self._default_source_names)
        if wanted <= 0:
            return

        rows, cols = self._grid_for(wanted)
        if (rows, cols) == (self.plot_grid.rows, self.plot_grid.cols):
            return

        self.plot_grid.update_grid(rows, cols)
        self.settings_panel.set_grid_size(rows, cols)

        if rows * cols < wanted:
            self.log_display_panel.log_message(
                "warning",
                f"The configuration lists {wanted} display sources but the plot "
                f"grid holds at most {rows * cols}; the extra sources will not "
                "be plotted.",
            )

    def showEvent(self, event):
        """Re-apply the budget once, against the height the window really got.

        _init_ui can only estimate from the screen; a tiling window manager, a
        restored geometry or a --geometry flag can all land somewhere else.
        """
        super().showEvent(event)
        if not getattr(self, "_budget_applied", False):
            self._budget_applied = True
            self._apply_vertical_budget(self.height())

    def _connect_client_signals(self):
        """Connect client signals to UI handlers."""
        self.client_worker.server_scan_completed.connect(self.on_server_scan_completed)
        self.client_worker.server_scan_completed.connect(
            self.status_bar.on_scan_complete
        )
        self.client_worker.server_connecting.connect(self.on_server_connecting)
        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)
        self.client_worker.server_lost.connect(self.on_server_lost)
        self.client_worker.server_diagnostics.connect(self.on_server_diagnostics)

        self.client_worker.server_scan_progress.connect(
            self.status_bar.update_scan_progress
        )

        self.client_worker.device_init_succeeded.connect(
            self.update_status_bar_and_buttons
        )
        self.client_worker.device_init_failed.connect(self.on_device_init_failed)
        self.client_worker.device_init_succeeded.connect(self.on_devices_ready)
        self.client_worker.device_init_succeeded.connect(self.on_device_init_succeeded)
        self.client_worker.device_init_report.connect(self.on_device_init_report)
        self.client_worker.device_init_progress.connect(self.on_device_init_progress)
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
            # The client answers asynchronously; these put the Balance button
            # back once the server reports the search has ended. Bound methods,
            # not lambdas: the finished signal is emitted from the thread pool,
            # and only a QObject receiver gets the queued connection that keeps
            # the widget touched on the GUI thread.
            self.client_worker.dpic_balance_started.connect(self.on_dpic_balance_started)
            self.client_worker.dpic_balance_finished.connect(
                self.on_dpic_balance_finished
            )
            self.client_worker.dpic_balance_progress.connect(
                self.on_dpic_balance_progress
            )

        self.settings_panel.log_event.connect(self.log_display_panel.log_message)
        self.plot_grid.log_event.connect(self.log_display_panel.log_message)

    def _connect_statusbar_signals(self):
        self.status_bar.network_scan_requested.connect(self.on_network_scan_requested)

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
            self.on_device_discovery_requested
        )

    def on_dpic_balance_started(self, device_id: str):
        self.settings_panel.set_balance_running(device_id, True)
        self.status_bar.show_activity(
            f"Balancing {device_id}…",
            level="info",
            slow_message=f"Still balancing {device_id} — the search sweeps "
            "phase and amplitude and can take a few minutes",
            slow_after_ms=self.BALANCE_SLOW_MS,
        )

    def on_dpic_balance_finished(self, device_id: str, ok: bool, message: str):
        self.settings_panel.set_balance_running(device_id, False)
        self.status_bar.show_activity(
            f"Balanced {device_id}" if ok else f"Balance failed on {device_id}",
            level="success" if ok else "error",
        )
        if not ok and message:
            QTimer.singleShot(
                0,
                lambda: self._show_error_dialog(
                    "DPIC balance",
                    f"The balance on {device_id} did not complete.",
                    message,
                ),
            )

    def on_dpic_balance_progress(self, device_id: str, progress: dict):
        self.settings_panel.apply_balance_progress(device_id, progress)
        # The sweep name and point count are the only evidence the search is
        # moving at all; the panel shows them on the button, but the button is
        # off-screen whenever the settings strip is scrolled elsewhere.
        stage = progress.get("stage")
        point, planned = progress.get("point"), progress.get("planned")
        if stage and point and planned:
            self.status_bar.show_activity(
                f"Balancing {device_id}: {stage} {point}/{planned}", level="info"
            )

    def on_server_scan_completed(self, servers: list):
        """Report a scan the user asked for. Background rescans stay silent.

        While the window is disconnected it re-scans every few seconds by
        itself; announcing each of those would put "No BioView servers found"
        on screen on a five-second loop and make the bar useless for anything
        that is actually happening.
        """
        if not self._scan_requested:
            return
        self._scan_requested = False

        count = len(servers or [])
        self.status_bar.show_activity(
            f"Found {count} BioView server(s)" if count else "No BioView servers found",
            level="success" if count else "warning",
        )

    def on_network_scan_requested(self):
        self._scan_requested = True
        self.status_bar.show_activity(
            "Searching the network for BioView servers…",
            level="info",
            slow_message="Still searching — no server has answered yet",
            slow_after_ms=self.SCAN_SLOW_MS,
        )
        self.client_worker.discover_servers()

    def on_server_connecting(self, label: str):
        self.status_bar.set_server_connecting(label)
        self.status_bar.show_activity(
            f"Connecting to {label}…",
            level="info",
            slow_message=f"Still connecting to {label} — check that the server "
            "is running and reachable",
            slow_after_ms=self.CONNECT_SLOW_MS,
        )

    def on_server_diagnostics(self, issues: list):
        """Put a server-level fault in front of the operator, once each.

        Filtered to the device types this configuration uses: the server
        reports every backend it could not load, and most of them are not
        this session's concern.
        """
        QTimer.singleShot(
            0,
            lambda: self._diagnostics.report(issues, self._configured_device_types),
        )

    def on_device_init_progress(self, group_status: dict, group_errors: dict):
        """Follow the device groups coming up one at a time.

        The server initializes them in sequence and has always reported each
        one as it lands; the window used to wait for the whole command to
        finish before touching the status bar, so a rig where the third group
        hangs looked identical to one that was simply slow.
        """
        for group_id, raw_status in group_status.items():
            if group_id == "metadata":
                continue

            status = raw_status
            if not isinstance(status, DeviceStatus):
                with contextlib.suppress(Exception):
                    status = DeviceStatus(raw_status)
            if not isinstance(status, DeviceStatus):
                continue

            if self._announced_group_status.get(group_id) == status:
                continue
            self._announced_group_status[group_id] = status

            self.device_status[group_id] = status
            self.status_bar.update_device_status(group_id, status)

            message, level = self._group_activity(group_id, status, group_errors)
            if message:
                self.status_bar.show_activity(message, level=level)

    @staticmethod
    def _group_activity(group_id, status: DeviceStatus, group_errors: dict):
        """What the bar should say about one group reaching ``status``."""
        if status == DeviceStatus.CONNECTING:
            return f"Connecting {group_id}…", "info"
        if status == DeviceStatus.CONNECTED:
            return f"{group_id} connected", "success"
        if status == DeviceStatus.UNAVAILABLE:
            reason = (group_errors or {}).get(group_id)
            return f"{group_id} failed" + (f": {reason}" if reason else ""), "error"
        return None, "info"

    def on_device_discovery_requested(self):
        self.status_bar.show_activity(
            "Discovering devices…",
            level="info",
            slow_message="Still discovering — enumerating hardware can take a while",
            slow_after_ms=self.DISCOVER_SLOW_MS,
        )
        self._announced_group_status.clear()
        self.client_worker.initialize_devices(True)

    def _handle_streaming_status_changed(self, is_streaming: bool):
        # Its own level, not "success": starting and stopping a stream is the
        # routine thing this application does, and green is reserved for
        # something having gone right.
        self.status_bar.show_activity(
            "Streaming started" if is_streaming else "Streaming stopped",
            level="streaming",
        )
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

    def _toggle_log_window(self):
        """Show or hide the log window, clearing the unseen badge when shown."""
        if self.log_window.toggle():
            self.command_bar.clear_log_badge()

    def _note_log_message(self, level, msg=None):
        """Badge the Log button for anything the user would want to see.

        Hiding the log behind a button must not make an error quieter, so a
        warning or error raised while the window is closed is counted on the
        button itself.
        """
        if not self.log_window.isVisible():
            self.command_bar.note_log_message(level, msg)

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

        self._apply_default_sources(source_objs)

    def _apply_default_sources(self, sources):
        """Plot the configured ``display_sources`` as they show up.

        The advertised source list only exists after the devices are
        initialized, so a configured default cannot be applied at startup; it
        is applied the first time a matching source is advertised.
        """
        if not self._default_source_names:
            return

        for source in sources:
            names = _source_aliases(source)
            wanted = names & self._default_source_names
            if not wanted or wanted & self._applied_default_sources:
                continue
            # Marked before the add, not after: a source that could not be
            # plotted (a full grid) must not reappear unbidden on the next
            # refresh.
            self._applied_default_sources |= wanted
            self.add_plot_source(source)

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

        # No timeout: initialization runs for up to a couple of minutes with a
        # USRP in the session, and a message that expires halfway through reads
        # as "nothing is happening". The per-group messages that follow replace
        # this one as each group is reached.
        self._announced_group_status.clear()
        self.status_bar.show_activity(
            "Initialization started…",
            level="info",
            slow_message="Still initializing — bringing up a USRP takes a minute",
            slow_after_ms=self.INIT_SLOW_MS,
        )

        self.client_worker.initialize_devices()

    def on_device_init_succeeded(self, _device_status=None):
        """At least one group came up. Partial failures are reported separately
        by ``on_device_init_report``, which knows which groups they were."""
        if not self._pending_init_failures:
            self.status_bar.show_activity("Initialized successfully!", level="success")

    def on_device_init_failed(self):
        """Reset UI when device initialization fails or times out."""
        for group_id in self.device_status:
            if group_id == "metadata":
                continue
            self.status_bar.update_device_status(group_id, DeviceStatus.DISCONNECTED)
        if self.client_worker:
            self.command_bar.update_button_states(self.client_worker.status)
        self.status_bar.show_activity("Initialization failed", level="error")

        # A per-group report explains itself and raises its own dialog. This is
        # the other case: the command failed outright, so no group has a state
        # and the only account of it is a log line the operator cannot see.
        if not self._pending_init_failures:
            reasons = "\n".join(
                f"{group}: {reason}"
                for group, reason in (self.client_worker.device_errors or {}).items()
            )
            QTimer.singleShot(
                0,
                lambda: self._show_error_dialog(
                    "Device initialization",
                    "No device group could be initialized.",
                    reasons
                    or "The server returned no device status. Check that it is "
                    "still running, then try again.",
                ),
            )

    def on_device_init_report(self, failures: dict, only_discover: bool):
        """Put a partial device failure in front of the operator.

        The log already carries every line of this, but a session starts with
        the log window closed and a half-initialized rig looks identical to a
        healthy one until a plot stays flat. Discovery is excluded: a device
        that is merely absent from a scan is not a failure worth a modal.
        """
        # Read by on_device_init_succeeded, which is emitted after this and
        # must not claim an unqualified success.
        self._pending_init_failures = dict(failures or {})

        if only_discover or not failures:
            return

        total = len([k for k in self.device_status if k != "metadata"])
        ok = max(0, total - len(failures))
        self.status_bar.show_activity(
            f"Initialized with errors ({ok}/{total} device groups ready)",
            level="warning",
        )

        # Queued, not shown inline: a modal spins its own event loop, and this
        # is running inside a signal emitted from the device-init worker.
        QTimer.singleShot(
            0, lambda: self._show_init_failure_dialog(dict(failures), ok, total)
        )

    def _show_init_failure_dialog(self, failures: dict, ok: int, total: int):
        detail = "\n\n".join(
            f"{group}:\n    {reason}" for group, reason in failures.items()
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Device initialization")
        box.setText(
            f"{len(failures)} of {total} device group(s) failed to initialize."
            if total
            else f"{len(failures)} device group(s) failed to initialize."
        )
        # The reasons go in the body rather than behind "Show Details": they
        # are the whole point of the dialog, and one extra click hides them
        # from exactly the operator who needed them.
        box.setInformativeText(
            f"{ok} group(s) are ready and the session can continue without "
            f"these:\n\n{detail}"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _show_error_dialog(self, title: str, text: str, detail: str = ""):
        """A modal for a failure that has no other way of reaching the user.

        Everything it shows has already been logged; the log window starts
        closed, which is exactly why this exists.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(text)
        if detail:
            box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

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

    def _check_routine_instructions(self):
        """Report unplayable instruction files, and name the audio outputs.

        A missing file only surfaces today as one error line at the moment the
        routine starts, by which point the recording is already running and
        silent. Both checks are cheap and both are done up front.
        """
        media_kinds = {"audio", "video"}
        missing = [
            (mode.label, mode.instruction.file)
            for mode in self.timed_modes
            if mode.instruction and not Path(mode.instruction.file).exists()
        ]
        for label, file_path in missing:
            self.log_display_panel.log_message(
                "warning",
                f"Routine '{label}': instruction file not found ({file_path}). "
                "It will not play.",
            )

        if not any(
            mode.instruction and mode.instruction.type in media_kinds
            for mode in self.timed_modes
        ):
            return

        outputs = list_audio_outputs()
        requested = self.experiment_config.get_param("audio_output_device", None)
        if not outputs:
            self.log_display_panel.log_message(
                "warning", "No audio output device is available; routines will be silent"
            )
            return

        self.log_display_panel.log_message(
            "info",
            "Audio outputs available: " + "; ".join(outputs),
        )
        if requested and not any(
            str(requested).strip().casefold() in name.casefold() for name in outputs
        ):
            self.log_display_panel.log_message(
                "warning",
                f"audio_output_device {requested!r} matches none of them; the "
                "default output will be used",
            )

    def _on_instruction_log(self, level: str, message: str):
        self.log_display_panel.log_message(level, message)
        # A routine that cannot play its instruction is otherwise indicated
        # only in a log window that is closed by default, while the recording
        # runs on regardless.
        if level == "error":
            self._show_toast(message, level="error")

    def _start_instruction(self, spec):
        self._stop_instruction()
        if spec is None:
            return
        self.instruction_controller = InstructionController(
            spec,
            host_widget=self,
            output_device=self.experiment_config.get_param("audio_output_device", None),
        )
        self.instruction_controller.log_event.connect(self._on_instruction_log)
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
        self.status_bar.show_activity(
            f"Connected to {Client.server_label(self.client_worker.selected_server)}",
            level="success",
        )

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
        self.status_bar.show_activity("Disconnected from server", level="warning")
        self._announced_group_status.clear()
        try:
            self.status_bar.set_server_status(ClientStatus.SERVER_DISCONNECTED)
        except Exception:
            self.log_display_panel.log_message("warning", "Unable to update status bar")

        self.available_sources = []
        self.settings_panel.set_available_sources([])
        self.plot_grid.clear_sources()

        self._forget_device_status()

        self.command_bar.update_button_states(self.client_worker.status)
        self.log_display_panel.log_message("warning", "Disconnected from server")

    def _forget_device_status(self):
        """Drop what we believed about the devices; the server is gone.

        Whatever was initialized belonged to a server this window can no longer
        reach. Keeping the old statuses on screen would leave the operator
        looking at devices that are ready according to the UI and absent
        according to everything else.
        """
        for group_id in self.device_status:
            self.device_status[group_id] = DeviceStatus.NOINIT
            with contextlib.suppress(Exception):
                self.status_bar.update_device_status(group_id, DeviceStatus.NOINIT)

    def _resume_reconnect_attempts(self, was_local: bool):
        """Restart the retry timers that stopped when this window first connected.

        Both stop themselves on success and nothing used to start them again,
        so a window that lost its server sat disconnected forever -- even once
        a server was back and answering on the very port it was watching.

        Only ever called for a server that *went away*, never for a disconnect
        the user asked for: the Monitor pointedly does not relatch onto
        localhost, so that someone who disconnected on purpose is not dragged
        straight back. And the localhost probe is only resumed for a window
        that was using the local server, or losing a LAN server would quietly
        move the window onto a different machine's.
        """
        timers = ["_rescan_timer"]
        if was_local:
            timers.append("_localhost_timer")

        for name in timers:
            timer = getattr(self, name, None)
            if timer is not None and not timer.isActive():
                with contextlib.suppress(Exception):
                    timer.start()

    def on_server_lost(self, server: dict):
        """The server went away by itself. Say so, and offer to start another.

        Only for a server this window is entitled to restart: a LAN server
        belongs to another machine, and the retry timers are the whole answer
        there.
        """
        self.log_display_panel.log_message("error", "The server stopped responding")
        self.status_bar.show_activity(
            "The server stopped responding", level="error", timeout_ms=0
        )

        was_local = _is_local_server(server)
        self._resume_reconnect_attempts(was_local)

        if not was_local:
            return

        dialog = ServerLostDialog(
            restart=lambda: launch.restart_server(self.control_port, self.data_port),
            parent=self,
        )
        outcome = dialog.exec()

        if outcome == ServerLostDialog.QUIT:
            self.close()
            return

        if outcome == QDialog.DialogCode.Accepted:
            self.log_display_panel.log_message("info", "Server restarted")
            # The re-armed localhost timer does the reconnecting; nudge it so
            # the window does not sit disconnected for another whole interval.
            with contextlib.suppress(Exception):
                self.client_worker.quick_connect_localhost()

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


def run_monitor(
    argv=None, control_port: int = CONTROL_PORT, data_port: int = DATA_PORT
) -> int:
    """Build the Qt application, show the window and run the event loop.

    The ports come from the launcher rather than the command line: it is the
    launcher that parsed them and started the server on them.

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
        control_port=control_port,
        data_port=data_port,
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

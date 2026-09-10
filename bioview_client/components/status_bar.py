import contextlib

import qtawesome as qta
from bioview_common import ClientStatus, DeviceStatus
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from bioview_client.constants.theme import get_connection_status_color, get_qcolor


class ServerConnector(QWidget):
    """Server picker: lists available servers and emits connection state."""

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
        self.scan_progress_bar.setFixedWidth(160)
        control_layout.addWidget(self.scan_progress_bar)

        # The controls are a fixed-size row, not a stretch bar: given the full
        # width of the flyout they would otherwise smear across it.
        control_layout.addStretch()

        self.connection_label = QLabel("Status: Disconnected")
        self.connection_label.setContentsMargins(6, 0, 6, 0)
        control_layout.addWidget(self.connection_label)

        self.setLayout(control_layout)

    def scan_network(self):
        # Toggle scanning state: if already scanning, request cancel
        if self._scanning:
            # Request cancel
            self.network_scan_cancel_requested.emit()
            return

        # begin scan
        self._scanning = True
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

    def on_scan_complete(self, discovered_servers: list[dict] = None):
        """On network scan completion, handler passes the discovered servers."""
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
                display_name = "Unnamed Server"

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
        # Ensure all active scans are stopped and all connections are closed.
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
    def __init__(self, device_status: dict):
        super().__init__()
        """``device_status`` is a flat {group_id: DeviceStatus} mapping."""
        self.device_widgets = {}

        # Create horizontal layout for all devices
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(15)

        # Add device widgets (keys are device ids)
        for group_id, group_status in device_status.items():
            self.add_device(group_id, group_status)

        self.setLayout(self.layout)

    # Handle theme changes
    def _update_icons(self):
        # TODO: Fix this logic.
        for device_id, device_map in self.devices.items():
            with contextlib.suppress(Exception):
                status = device_map.get("status", DeviceStatus.DISCONNECTED)
                self.device_widgets[device_id] = status

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def add_device(self, group_id, group_status=DeviceStatus.DISCONNECTED):
        device_widget = DeviceStatusWidget(group_id, group_status)
        self.device_widgets[group_id] = device_widget
        self.layout.addWidget(device_widget)

    def update_device_status(self, group_id, new_status):
        """Set one group's indicator, adding it if the bar has not seen it yet.

        A group the panel was not built with used to be dropped in silence,
        which is exactly what happened to a configuration loaded after the
        window opened: the server reported it, and nothing showed it.
        """
        widget = self.device_widgets.get(group_id, None)

        if widget is None:
            self.add_device(group_id, new_status)
            return

        widget.update_status(new_status)


class RoutineProgressBar(QWidget):
    """Compact progress indicator for a running timed-mode routine, shown in the
    bottom status bar. Hidden unless a routine is active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 1.0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        self.label = QLabel("")
        layout.addWidget(self.label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        self.bar.setFixedWidth(180)
        layout.addWidget(self.bar)

        self.time_label = QLabel("")
        layout.addWidget(self.time_label)

        self.setLayout(layout)
        self.setVisible(False)

    @staticmethod
    def _fmt_remaining(elapsed: float, duration: float) -> str:
        remaining = max(0, int(round(duration - elapsed)))
        minutes, seconds = divmod(remaining, 60)
        return f"{minutes:d}:{seconds:02d}"

    def start(self, label: str, duration: float):
        self._duration = max(0.001, float(duration))
        self.label.setText(f"\u25b6 {label}")
        self.bar.setValue(0)
        self.time_label.setText(self._fmt_remaining(0.0, self._duration))
        self.setVisible(True)

    def update_progress(self, elapsed: float, duration: float):
        frac = max(0.0, min(1.0, elapsed / max(0.001, duration)))
        self.bar.setValue(int(frac * 1000))
        self.time_label.setText(self._fmt_remaining(elapsed, duration))

    def stop(self):
        self.setVisible(False)


class ServerConnectionFlyout(QDialog):
    """Bottom sheet holding the server picker.

    The status bar carries a single fact -- whether the server is there -- and
    everything needed to change that lives in here, one click away. Modal,
    because half-finished connection changes are exactly what a permanently
    visible picker invited; it spans the window and sits against its bottom
    edge, so it rises out of the status line that opened it.
    """

    #: Gap left between the sheet and the window edges it is anchored to.
    MARGIN = 12

    def __init__(self, connector: "ServerConnector", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Server Connection")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)

        # Frameless: the sheet has to draw its own edge, or it reads as widgets
        # floating loose over the plots.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.sheet = QFrame()
        self.sheet.setObjectName("serverSheet")
        self.sheet.setFrameShape(QFrame.Shape.StyledPanel)
        outer.addWidget(self.sheet)

        layout = QVBoxLayout(self.sheet)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("Server Connection")
        self.title_label.setStyleSheet("font-weight: 600;")
        header.addWidget(self.title_label)
        header.addStretch()

        # Frameless, so the sheet has to carry its own way out.
        self.close_button = QPushButton()
        self.close_button.setIcon(qta.icon("fa6s.xmark", color=get_qcolor("red")))
        self.close_button.setToolTip("Close")
        self.close_button.setFlat(True)
        # Off the focus chain, so opening the sheet does not land the caret on
        # the one control that throws the sheet away.
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self.reject)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        # The status bar's own connector, not a copy of it: it holds the scan
        # state, the discovered servers and every signal the monitor is
        # already wired to.
        self.connector = connector
        layout.addWidget(connector)

    def _update_icons(self):
        self.close_button.setIcon(qta.icon("fa6s.xmark", color=get_qcolor("red")))

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def reanchor(self):
        """Sit flush against the bottom edge of the window that owns the bar."""
        parent = self.parentWidget()
        if parent is None:
            return
        window = parent.window()
        self.setFixedWidth(max(360, window.width() - 2 * self.MARGIN))
        self.adjustSize()

        bottom_left = window.mapToGlobal(window.rect().bottomLeft())
        self.move(
            bottom_left.x() + self.MARGIN,
            bottom_left.y() - self.height() - self.MARGIN,
        )

    def showEvent(self, event):
        super().showEvent(event)
        # After the show, so the sheet is measured at the size it will render
        # at rather than at its pre-layout hint.
        self.reanchor()


class StatusBar(QStatusBar):
    network_scan_requested = pyqtSignal()

    #: The one server fact the bar itself carries, per ClientStatus.
    SERVER_TEXT = {
        ClientStatus.SERVER_CONNECTED: "BioView Server Connected",
        ClientStatus.SCANNING: "Searching for BioView Servers…",
    }
    SERVER_TEXT_DEFAULT = "BioView Server Disconnected"

    #: How long a completed-state message stays up before the bar goes quiet.
    ACTIVITY_TIMEOUT_MS = 4000

    #: How long an open-ended operation runs before the bar says so. Below
    #: this, a message that changes itself reads as a glitch; above it, an
    #: unchanging message reads as a hang.
    SLOW_ACTIVITY_MS = 8000

    #: Theme colour per activity level. "streaming" is its own level rather
    #: than a success: starting and stopping a stream is the routine thing this
    #: application does, and reporting it in the same green as "the devices
    #: came up" spends the colour that should mean something went right. "info"
    #: shares that neutral yellow: a plain notice is neither progress nor a
    #: problem, and blue read as a state of its own next to the indicators.
    ACTIVITY_COLORS = {
        "info": "yellow",
        "success": "green",
        "warning": "orange",
        "error": "red",
        "streaming": "yellow",
    }

    #: Levels whose colour is knocked back from the palette value. The theme's
    #: yellow is a full-strength alert colour; a routine state change wants the
    #: same hue at a lower voice.
    _MUTED_LEVELS = {"streaming", "info"}
    _MUTED_FACTOR = 135

    #: The bar reports state; it is not itself a control. Only the widgets that
    #: actually do something take a hover, and the frames Qt draws around
    #: status-bar items are removed so the strip reads as one flat surface.
    _BAR_STYLE = """
        QStatusBar { background: transparent; border: none; }
        QStatusBar::item { border: none; }
    """

    def __init__(self, device_status: dict = None, parent=...):
        super().__init__(parent)
        self.setStyleSheet(self._BAR_STYLE)
        # The grip is the one part of the bar that lights up under the cursor
        # without being a control anyone uses: the window resizes from its
        # edges regardless.
        self.setSizeGripEnabled(False)

        # Use a QWidget with a layout to group widgets
        self.container = QWidget()
        self._layout = QHBoxLayout(self.container)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # A scan button, a server dropdown and three more buttons used to live
        # here permanently, for a decision that is made once a session. The bar
        # now states the outcome and hands the controls to a flyout.
        self.server_indicator = StatusIndicator(DeviceStatus.UNAVAILABLE)
        self._layout.addWidget(
            self.server_indicator, alignment=Qt.AlignmentFlag.AlignLeft
        )

        self.server_status_label = QLabel(self.SERVER_TEXT_DEFAULT)
        self.server_status_label.setContentsMargins(6, 0, 6, 0)
        self._layout.addWidget(
            self.server_status_label, alignment=Qt.AlignmentFlag.AlignLeft
        )

        self.server_connector = ServerConnector()
        self.server_flyout = ServerConnectionFlyout(self.server_connector, parent=self)

        self.more_button = QPushButton(" More…")
        self.more_button.setToolTip("Choose and connect to a BioView server")
        # Not flat. A flat button has no border until the cursor reaches it,
        # and then paints a bare rectangle of highlight into an otherwise
        # empty strip -- which is what made the *bar* look like the thing
        # lighting up. Given an ordinary button frame the highlight lands
        # inside a shape that was already visibly a button, so the hover reads
        # as the button's and the bar around it stays quiet.
        self.more_button.setFlat(False)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Sized to its label so the frame does not stretch across the bar.
        self.more_button.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.more_button.clicked.connect(self.open_server_flyout)
        self._layout.addWidget(self.more_button, alignment=Qt.AlignmentFlag.AlignLeft)

        # Transient state messages ("Initializing devices…", "Initialized
        # successfully"). Empty, and taking no space, whenever nothing is
        # happening.
        self.activity_label = QLabel("")
        self.activity_label.setContentsMargins(12, 0, 6, 0)
        self._layout.addWidget(self.activity_label, alignment=Qt.AlignmentFlag.AlignLeft)

        # Clears a completed-state message after ACTIVITY_TIMEOUT_MS. Single
        # shot and restarted per message, so a burst of updates leaves only the
        # last one on screen for its full time.
        self._activity_timer = QTimer(self)
        self._activity_timer.setSingleShot(True)
        self._activity_timer.timeout.connect(self.clear_activity)

        # Replaces an open-ended message with a "still working" one once the
        # operation has run long enough that the first message stops being
        # reassuring. Armed only when the caller supplies the second wording.
        self._slow_timer = QTimer(self)
        self._slow_timer.setSingleShot(True)
        self._slow_timer.timeout.connect(self._on_activity_slow)
        self._slow_message = None
        self._slow_level = "warning"

        self._update_icons()
        self._layout.addStretch()

        # Timed-mode progress indicator (centered, hidden until a routine runs)
        self.routine_progress = RoutineProgressBar()
        self._layout.addWidget(
            self.routine_progress, alignment=Qt.AlignmentFlag.AlignCenter
        )
        self._layout.addStretch()

        self.device_status_panel = DeviceStatusPanel(device_status=device_status)

        self._layout.addWidget(
            self.device_status_panel, alignment=Qt.AlignmentFlag.AlignRight
        )

        self.container.setLayout(self._layout)

        # Everything above except the buttons is a readout. Taking them off the
        # mouse means the cursor crossing the bar cannot put any of them into a
        # hover state, while the buttons keep their own.
        self._make_readouts_inert()

        self.addPermanentWidget(self.container, stretch=1)

        # Forward signals and callbacks from components
        self._forward_signals()
        self._forward_callbacks()

        # Update UI to reflect connection state
        self.server_connector.server_connection_state_updated.connect(
            lambda status: self.set_server_status(status)
        )

    def _make_readouts_inert(self):
        """Take the non-interactive widgets in the bar off the mouse.

        The bar carries labels, indicators and a progress bar that do nothing
        when clicked. Left mouse-visible they each accept enter/leave events,
        which is what made moving across the strip feel like hovering a
        control. ``self.container`` itself stays interactive so the buttons
        inside it keep working.
        """
        inert = (
            self.server_indicator,
            self.server_status_label,
            self.activity_label,
            self.routine_progress,
            self.device_status_panel,
        )
        for widget in inert:
            with contextlib.suppress(Exception):
                widget.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )

    # Transient activity messages
    @classmethod
    def activity_color(cls, level: str) -> QColor:
        """Colour for an activity level, muted where the level asks for it."""
        colour = get_qcolor(cls.ACTIVITY_COLORS.get(level, "yellow"))
        if level in cls._MUTED_LEVELS:
            colour = colour.darker(cls._MUTED_FACTOR)
        return colour

    def _paint_activity(self, message: str, level: str):
        self.activity_label.setText(message)
        self.activity_label.setStyleSheet(
            f"color: {self.activity_color(level).name()}; font-weight: 600;"
        )

    def show_activity(
        self,
        message: str,
        level: str = "info",
        timeout_ms: int | None = None,
        slow_message: str | None = None,
        slow_after_ms: int | None = None,
        slow_level: str = "warning",
    ):
        """Show a state-change message in the bar.

        ``timeout_ms`` of 0 (the default for ``info``) leaves the message up
        until it is replaced or cleared -- an operation that is still running
        should not stop announcing itself halfway through. A finished state
        passes a timeout so the bar goes quiet again on its own.

        ``slow_message`` is what the bar says if the operation is still running
        ``slow_after_ms`` later. Connecting, initializing and balancing all
        have a normal duration and a duration that means something is wrong,
        and the difference is invisible from a message that never changes. The
        escalation is cancelled by the next message, whatever it is, so the
        second wording can only appear while the first one is still true.
        """
        self._paint_activity(message, level)

        self._activity_timer.stop()
        self._slow_timer.stop()
        self._slow_message = None

        if timeout_ms is None:
            timeout_ms = 0 if level == "info" else self.ACTIVITY_TIMEOUT_MS
        if timeout_ms > 0:
            self._activity_timer.start(int(timeout_ms))

        if slow_message:
            self._slow_message = slow_message
            self._slow_level = slow_level
            self._slow_timer.start(int(slow_after_ms or self.SLOW_ACTIVITY_MS))

    def _on_activity_slow(self):
        """The operation is still running; say so rather than repeating itself."""
        if self._slow_message:
            self._paint_activity(self._slow_message, self._slow_level)
            self._slow_message = None

    def clear_activity(self):
        self._activity_timer.stop()
        self._slow_timer.stop()
        self._slow_message = None
        self.activity_label.setText("")

    def _forward_signals(self):
        # Re-exposed from the embedded ServerConnector.
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

    def open_server_flyout(self):
        """Raise the bottom sheet holding the server picker."""
        self.server_flyout.reanchor()
        self.server_flyout.show()
        self.server_flyout.raise_()
        self.server_flyout.activateWindow()

    def _update_icons(self):
        self.more_button.setIcon(qta.icon("fa6s.circle-info", color=get_qcolor("blue")))

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def _set_summary(self, status: ClientStatus):
        """Update the one-line server summary the bar shows."""
        self.server_status_label.setText(
            self.SERVER_TEXT.get(status, self.SERVER_TEXT_DEFAULT)
        )
        if status == ClientStatus.SERVER_CONNECTED:
            colour, indicator = "green", DeviceStatus.CONNECTED
        elif status == ClientStatus.SCANNING:
            colour, indicator = "yellow", DeviceStatus.CONNECTING
        else:
            colour, indicator = "red", DeviceStatus.UNAVAILABLE
        self.server_status_label.setStyleSheet(f"color: {get_qcolor(colour).name()}")
        self.server_indicator.update_status(indicator)

    def set_server_connecting(self, label: str = ""):
        """Say that a connection attempt is in flight, and to which server.

        Not a ClientStatus: the client is not connected yet and nothing about
        its state has changed, so this is only how the bar reads while the
        handshake runs.
        """
        self.server_status_label.setText(
            f"Connecting to {label}…" if label else "Connecting to BioView Server…"
        )
        self.server_status_label.setStyleSheet(f"color: {get_qcolor('yellow').name()}")
        self.server_indicator.update_status(DeviceStatus.CONNECTING)

    def set_server_status(self, status: ClientStatus):
        """Centralize server-related UI updates based on ClientStatus."""
        self._set_summary(status)

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
                # Other server states: neutral text; allow connect if choices exist
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

    def update_device_status(self, group_id, new_status):
        self.device_status_panel.update_device_status(group_id, new_status)

    # Timed-mode routine progress helpers
    def start_routine(self, label: str, duration: float):
        self.routine_progress.start(label, duration)

    def update_routine(self, elapsed: float, duration: float):
        self.routine_progress.update_progress(elapsed, duration)

    def stop_routine(self):
        self.routine_progress.stop()

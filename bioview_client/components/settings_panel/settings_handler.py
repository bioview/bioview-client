from bioview_common.datatypes import SUPPORTED_CONFIGURATION_TYPES
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .common_settings import CommonSettingsPanel
from .device_settings import (
    BIOPACSettingsPanel,
    DummySettingsPanel,
    MicrophoneSettingsPanel,
    USRPSettingsPanel,
)
from .panel_utils import DEFAULT_MAX_PANEL_HEIGHT, wrap_scrollable


def _panel_weight(config):
    """The strip share a configuration block asked for, or None.

    Non-positive and unparseable values are ignored rather than collapsing a
    panel to nothing: a typo in a config file should cost the default layout,
    not a panel the user cannot reach.
    """
    raw = config.get_param("panel_width", None)
    if raw is None:
        return None
    try:
        weight = float(raw)
    except (TypeError, ValueError):
        return None
    return weight if weight > 0 else None


SETTINGS_PANEL_MAPPING = {
    SUPPORTED_CONFIGURATION_TYPES.USRP: USRPSettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.BIOPAC: BIOPACSettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.MICROPHONE: MicrophoneSettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.DUMMY: DummySettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.EXPERIMENT: CommonSettingsPanel,
}


class SettingsPanel(QWidget):
    """Every instrument's settings side by side, in a horizontal strip.

    This was a tab widget: one instrument visible at a time, and a tab bar plus
    its surrounding padding costing ~36 px of height that the plots could have
    had. Panels are now laid out next to each other, as many as the width can
    show at a readable size (see ``BREAKPOINTS``), and any that do not fit are
    reached by scrolling the strip sideways -- so a two-radio rig shows both
    instruments at once instead of asking which one to look at.

    Card width is driven from the viewport, not from the panels: each visible
    column is an exact share of it, so the strip always ends flush with the
    window edge and a partially visible card is a deliberate signal that there
    is more to the right.

    Equal columns are only a default. A configuration block can claim its own
    share of the strip with ``panel_width`` (see the configuration reference),
    because the panels are not equally hungry: an experiment block is five
    short rows while a two-channel USRP is a wide parameter grid.
    """

    update_device_param = pyqtSignal(str, str)
    device_param_changed = pyqtSignal(str, str, object)
    run_dpic_balance = pyqtSignal(str)
    log_event = pyqtSignal(str, str)

    #: (max width in px, columns shown). Above the last entry, four columns.
    BREAKPOINTS = ((900, 1), (1300, 2), (1750, 3))
    MAX_COLUMNS = 4

    #: The only gap between cards; the strip itself has no margins, so no
    #: height or width is spent on padding around the panels.
    CARD_SPACING = 6

    #: Floor for a card sized from an explicit ``panel_width`` share. A small
    #: share on a narrow window must not squeeze a panel past legibility; the
    #: strip scrolls sideways instead.
    MIN_WEIGHTED_CARD_WIDTH = 200

    def __init__(self, configurations):
        super().__init__()

        # Only get valid configurations
        configurations = {
            k: v
            for k, v in configurations.items()
            if v.get_type() in SUPPORTED_CONFIGURATION_TYPES
        }

        # Hold all panels as a dictionary for easy access
        self.setting_widgets = {}

        # Reference to the experiment (common) settings panel for source/save UI
        self.experiment_panel = None

        # The scrollable cards, in the order they were configured, and the
        # width share each one asked for (None where it asked for nothing).
        self._cards = []
        self._card_weights = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._strip = QScrollArea()
        self._strip.setWidgetResizable(True)
        self._strip.setFrameShape(QScrollArea.Shape.NoFrame)
        self._strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The cards scroll internally; a second vertical bar on the strip would
        # only ever scroll empty space.
        self._strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self._row = QHBoxLayout(container)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(self.CARD_SPACING)
        self._strip.setWidget(container)
        outer.addWidget(self._strip)

        for cfg_id, config in configurations.items():
            cfg_type = config.get_type()
            widget = SETTINGS_PANEL_MAPPING[cfg_type](config)
            # The group box carries the identity the tab bar used to: the
            # configuration id, which names the group in every other panel,
            # rather than the device name inside it.
            widget.setTitle(f"{cfg_id} Settings")

            card = wrap_scrollable(widget, max_height=DEFAULT_MAX_PANEL_HEIGHT)
            self._row.addWidget(card)
            self._cards.append(card)
            self._card_weights.append(_panel_weight(config))

            if cfg_type == SUPPORTED_CONFIGURATION_TYPES.EXPERIMENT:
                self.experiment_panel = widget

            # Enable logging
            widget.log_event.connect(self.send_to_log)

            # Forward device parameter tweaks (tagged with the configuration id)
            if hasattr(widget, "device_param_changed"):
                widget.cfg_id = cfg_id
                widget.device_param_changed.connect(self.device_param_changed)

            if hasattr(widget, "run_dpic_balance"):
                widget.run_dpic_balance.connect(self.run_dpic_balance.emit)

            signal_dict = widget.get_emittable_signals()
            for signal_name, signal_callback in signal_dict.items():
                if signal_name in ("update_device_param", "run_dpic_balance"):
                    continue
                if not hasattr(self, signal_name):
                    setattr(self, signal_name, signal_callback)

            # Add widget to dict
            self.setting_widgets[cfg_id] = widget

        self.setMaximumHeight(DEFAULT_MAX_PANEL_HEIGHT)
        self._relayout_cards()

    # Layout

    def _visible_columns(self, width: int) -> int:
        for max_width, columns in self.BREAKPOINTS:
            if width < max_width:
                return columns
        return self.MAX_COLUMNS

    def _weighted_widths(self, width: int) -> list[int] | None:
        """Card widths from the ``panel_width`` shares the configs asked for.

        Returns None when no configuration claimed a share, which leaves the
        breakpoint layout in charge. Every panel is on screen at once here:
        naming the shares only makes sense if they are all visible together.
        """
        if not any(w is not None for w in self._card_weights):
            return None

        # A panel that named no share takes the average of those that did, so
        # a partly annotated configuration still lays out sensibly.
        named = [w for w in self._card_weights if w is not None]
        fill = sum(named) / len(named)
        weights = [fill if w is None else w for w in self._card_weights]

        total = sum(weights)
        if total <= 0:
            return None

        gaps = self.CARD_SPACING * (len(weights) - 1)
        available = max(1, width - gaps)
        return [
            max(self.MIN_WEIGHTED_CARD_WIDTH, int(available * w / total))
            for w in weights
        ]

    def _relayout_cards(self):
        """Size each card to its share of the strip's viewport."""
        if not self._cards:
            return

        width = self._strip.viewport().width()
        if width <= 0:
            return

        widths = self._weighted_widths(width)
        if widths is None:
            # Fewer instruments than the width could show: they share the whole
            # strip rather than leaving a gap at the right.
            columns = min(self._visible_columns(width), len(self._cards))
            gaps = self.CARD_SPACING * (columns - 1)
            widths = [max(1, (width - gaps) // columns)] * len(self._cards)

        # Cards follow the strip's own height, so whatever the monitor's
        # vertical budget hands the panel goes to the controls rather than to
        # a fixed-height card sitting inside a taller empty strip.
        height = max(1, self._strip.viewport().height())
        for card, card_width in zip(self._cards, widths, strict=True):
            card.setFixedWidth(card_width)
            card.setMaximumHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_cards()

    def showEvent(self, event):
        """Re-size against the width the strip really got, not the hint."""
        super().showEvent(event)
        self._relayout_cards()

    # Panel forwarding

    def update_source(self, action, source):
        """Forward selection state updates to the experiment settings panel."""
        if self.experiment_panel is not None:
            self.experiment_panel.update_source(action, source)

    def set_available_sources(self, sources):
        """Populate the experiment panel's plot-source selector."""
        if self.experiment_panel is not None:
            self.experiment_panel.set_available_sources(sources)

    def set_grid_size(self, rows: int, cols: int):
        """Reflect a plot-grid layout chosen elsewhere in the spin boxes."""
        if self.experiment_panel is not None and hasattr(
            self.experiment_panel, "set_grid_size"
        ):
            self.experiment_panel.set_grid_size(rows, cols)

    def send_to_log(self, level, msg):
        self.log_event.emit(level, msg)

    def set_streaming_locked(self, locked: bool):
        for widget in self.setting_widgets.values():
            if hasattr(widget, "set_streaming_locked"):
                widget.set_streaming_locked(locked)

    def set_balance_running(self, cfg_id: str, running: bool):
        """Disable one device's Balance button while its search is in flight."""
        widget = self.setting_widgets.get(cfg_id)
        if widget is not None and hasattr(widget, "set_balance_running"):
            widget.set_balance_running(running)

    def apply_balance_progress(self, cfg_id: str, progress: dict):
        """Show a running balance's live values on the device that is running it."""
        widget = self.setting_widgets.get(cfg_id)
        if widget is not None and hasattr(widget, "apply_balance_progress"):
            widget.apply_balance_progress(progress)

from typing import Dict 

from PyQt6.QtCore import pyqtSignal, QObject
from PyQt6.QtWidgets import QTabWidget, QGroupBox

from bioview_common.datatypes import Configuration, SUPPORTED_CONFIGURATION_TYPES

from .common_settings import CommonSettingsPanel
from .device_settings import USRPSettingsPanel, BIOPACSettingsPanel, DummySettingsPanel
from .panel_utils import DEFAULT_MAX_PANEL_HEIGHT, wrap_scrollable

SETTINGS_PANEL_MAPPING = {
    SUPPORTED_CONFIGURATION_TYPES.USRP: USRPSettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.BIOPAC: BIOPACSettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.DUMMY: DummySettingsPanel,
    SUPPORTED_CONFIGURATION_TYPES.EXPERIMENT: CommonSettingsPanel
}

class SettingsPanel(QTabWidget):
    update_device_param = pyqtSignal(str, str)
    device_param_changed = pyqtSignal(str, str, object)
    run_dpic_balance = pyqtSignal(str)
    log_event = pyqtSignal(str, str)

    def __init__(self, configurations):
        super().__init__()

        # Only get valid configurations 
        configurations = {k: v for k, v in configurations.items() if v.get_type() in SUPPORTED_CONFIGURATION_TYPES}

        # Hold all panels as a dictionary for easy access
        self.setting_widgets = {} 

        # Reference to the experiment (common) settings panel for source/save UI
        self.experiment_panel = None

        for cfg_id, config in configurations.items(): 
            cfg_type = config.get_type() 
            widget = SETTINGS_PANEL_MAPPING[cfg_type](config)
            scroll = wrap_scrollable(widget, max_height=DEFAULT_MAX_PANEL_HEIGHT)
            self.addTab(scroll, f"{cfg_id} Settings") # Add to UI

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

        self.setMaximumHeight(DEFAULT_MAX_PANEL_HEIGHT + 36)

        # Add styling
        self.setTabPosition(QTabWidget.TabPosition.South)
        self.setTabShape(QTabWidget.TabShape.Rounded)
        self.setStyleSheet(
            """
            QTabBar::tab {
                border-width: 0;
                border: none;
                padding: 5px 10px;
                color: lightgray;
            }

            QTabBar::tab:selected {
                background: rgb(24, 25, 27);
                color: white;
            }

            QTabWidget::pane {
                border: none;
            }
            """
        )
    
    def _route_signals(self, source, signal, *args): 
        pass

    def update_source(self, action, source):
        """Forward selection state updates to the experiment settings panel."""
        if self.experiment_panel is not None:
            self.experiment_panel.update_source(action, source)

    def set_available_sources(self, sources):
        """Populate the experiment panel's plot-source selector."""
        if self.experiment_panel is not None:
            self.experiment_panel.set_available_sources(sources)

    def send_to_log(self, level, msg):
        self.log_event.emit(level, msg)

    def set_streaming_locked(self, locked: bool):
        for widget in self.setting_widgets.values():
            if hasattr(widget, "set_streaming_locked"):
                widget.set_streaming_locked(locked)

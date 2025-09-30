from typing import Dict

from bioview_common.datatypes import Configuration
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabWidget

from .common_settings import CommonSettingsPanel
from .device_settings import get_device_settings_panel


class SettingsPanel(QTabWidget):
    log_event = pyqtSignal(str, str)

    def __init__(
        self,
        common_config: Configuration = None,
        group_configs: Dict[str, Dict] = None,
    ):
        super().__init__()

        # Add common panel if applicable
        self.common_settings_panel = None

        if common_config is not None:
            self.common_settings_panel = CommonSettingsPanel(common_config)
            self.addTab(self.common_settings_panel, "Settings")  # Add to UI

            # Connect signals
            # self.common_settings_panel.parameter_changed.connect()
            self.common_settings_panel.log_event.connect(self.send_to_log)
            self.display_duration_changed = (
                self.common_settings_panel.display_duration_changed
            )
            self.grid_layout_changed = self.common_settings_panel.grid_layout_changed
            self.add_data_source = self.common_settings_panel.add_data_source
            self.remove_data_source = self.common_settings_panel.remove_data_source

            # Connect functions
            self.update_source = self.common_settings_panel.update_source

        # Create panels for connected devices
        self.device_settings_panel = {}

        # Group configs are Dicts of device configuration dicts
        for device_group_id, device_group_config in group_configs.items():
            # device_group_config will always be a dictionary since we always have metadata
            # If group contains multiple devices, create a tab per device with label 'group/device'
            for item_key, item_dict in device_group_config.items():
                if item_key == "metadata":
                    continue
                tab_label = f"{device_group_id}/{item_key}"
                dev_panel = get_device_settings_panel(item_dict)

                if dev_panel:
                    self.device_settings_panel[tab_label] = dev_panel
                    self.addTab(dev_panel, tab_label)
                    dev_panel.log_event.connect(self.send_to_log)

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

        # TODO: assign devices to specific groups

    def send_to_log(self):
        pass

from typing import Dict

from bioview_common.datatypes import Configuration
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabWidget

from .common_settings import CommonSettingsPanel
from .device_settings import DeviceSettingsPanel


class SettingsPanel(QTabWidget):
    log_event = pyqtSignal(str, str)

    def __init__(
        self,
        common_config: Configuration = None,
        device_config: Dict[str, Configuration] = None,
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
        if device_config is not None:
            for device_group_id, device_group_config in device_config.items():
                dev_panel = DeviceSettingsPanel(device_group_config)
                self.device_settings_panel[device_group_id] = dev_panel
                self.addTab(dev_panel, device_group_id)

                # TODO: Connect signals for update_param
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

    def get_sample_config(self):
        return {
            "groupA": [
                Configuration(
                    config_dict={
                        "parameters": {
                            "device_name": "Device 9",
                            "device_type": "usrp",
                            "txGain": 9,
                            "rxGain": 4,
                            "IFAmplitude": 1,
                        },
                        "ui_parameters": {
                            "txGain": {
                                "display_label": "TX Gain (dB)",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 50],
                                "step": 2,
                            },
                            "rxGain": {
                                "display_label": "RX Gain (dB)",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 50],
                                "step": 2,
                            },
                            "IFAmplitude": {
                                "display_label": "IF Amplitude",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                        },
                    }
                ),
                Configuration(
                    config_dict={
                        "parameters": {
                            "device_name": "Device 4",
                            "device_type": "usrp",
                            "txGain": 8,
                            "rxGain": 10,
                            "IFAmplitude": 4,
                        },
                        "ui_parameters": {
                            "txGain": {
                                "display_label": "TX Gain (dB)",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                                "value": 8,
                            },
                            "rxGain": {
                                "display_label": "RX Gain (dB)",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                                "value": 10,
                            },
                            "IFAmplitude": {
                                "display_label": "IF Amplitude",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                                "value": 4,
                            },
                        },
                    }
                ),
                Configuration(
                    config_dict={
                        "parameters": {
                            "device_name": "Device 1",
                            "device_type": "usrp",
                            "txGain": 3,
                            "rxGain": 8,
                            "IFAmplitude": 7,
                        },
                        "ui_parameters": {
                            "txGain": {
                                "display_label": "TX Gain (dB)",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                            "rxGain": {
                                "display_label": "RX Gain (dB)",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                            "IFAmplitude": {
                                "display_label": "IF Amplitude",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                        },
                    }
                ),
            ],
            "groupB": [
                Configuration(
                    config_dict={
                        "parameters": {
                            "device_name": "Device 2",
                            "device_type": "terp",
                            "prefix": 1.8,
                            "anod": 4,
                        },
                        "ui_parameters": {
                            "prefix": {
                                "display_label": "Log Prefix",
                                "display_type": "text",
                                "multiplier": 1,
                            },
                            "anod": {
                                "display_label": "Anodization",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                        },
                    }
                )
            ],
            "groupC": [
                Configuration(
                    config_dict={
                        "parameters": {
                            "device_name": "Device 3",
                            "device_type": "maxx",
                            "sliderA": 2,
                            "sliderB": 4,
                            "inputA": 3.5,
                            "inputB": 2.7,
                        },
                        "ui_parameters": {
                            "sliderA": {
                                "display_label": "Slider A",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                            "sliderB": {
                                "display_label": "Slider B",
                                "display_type": "slider",
                                "multiplier": 1,
                                "range": [1, 10],
                                "step": 1,
                            },
                            "inputA": {
                                "display_label": "Text A",
                                "display_type": "text",
                                "multiplier": 2,
                            },
                            "inputB": {
                                "display_label": "Text B",
                                "display_type": "text",
                                "multiplier": 2,
                            },
                        },
                    }
                )
            ],
        }

from PyQt6.QtWidgets import QTabWidget
from typing import List, Dict

from bioview_common.datatypes import Configuration

from .common_settings import CommonSettingsPanel
from .device_settings import DeviceSettingsPanel

class SettingsPanel(QTabWidget): 
    def __init__(
            self,
            file_name: str,
            save_dir: str,
            device_config: Dict[str, Dict] = {}
        ):
        super().__init__()
        device_config = self.get_sample_config()
        
        self.common_settings_panel = CommonSettingsPanel(file_name, save_dir)
        self.setTabPosition(QTabWidget.TabPosition.South)
        self.setTabShape(QTabWidget.TabShape.Rounded)
        self.setStyleSheet("""
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
            """)
        
        # Create panel for common settings
        self.addTab(self.common_settings_panel, "Common Settings")
        
        # Create panels for connected devices
        # TODO: assign devices to specific groups
        for groupId, devices in device_config.items():
            for device in devices:
                self.addTab(DeviceSettingsPanel(device.get_param("device_name"), device), device.get_param("device_name"))
            
        
    def get_sample_config(self):
        return {
            "groupA": [
                Configuration(config_dict={
                    "parameters": {
                        "device_name": "Device 9",
                        "device_type": "usrp",
                        "txGain": 9,
                        "rxGain": 4,
                        "IFAmplitude": 1
                    },
                    "ui_parameters": {
                        "txGain": {
                            "display_label": "TX Gain (dB)",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 50],
                            "step": 2
                        },
                        "rxGain": {
                            "display_label": "RX Gain (dB)",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 50],
                            "step": 2
                        },
                        "IFAmplitude": {
                            "display_label": "IF Amplitude",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        }
                    }
                }),
                Configuration(config_dict={
                    "parameters": {
                        "device_name": "Device 4",
                        "device_type": "usrp",
                        "txGain": 8,
                        "rxGain": 10,
                        "IFAmplitude": 4
                    },
                    "ui_parameters": {
                        "txGain": {
                            "display_label": "TX Gain (dB)",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1,
                            "value": 8
                        },
                        "rxGain": {
                            "display_label": "RX Gain (dB)",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1,
                            "value": 10
                        },
                        "IFAmplitude": {
                            "display_label": "IF Amplitude",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1,
                            "value": 4
                        }
                    }
                }),
                Configuration(config_dict={
                    "parameters": {
                        "device_name": "Device 1",
                        "device_type": "usrp",
                        "txGain": 3,
                        "rxGain": 8,
                        "IFAmplitude": 7
                    },
                    "ui_parameters": {
                        "txGain": {
                            "display_label": "TX Gain (dB)",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        },
                        "rxGain": {
                            "display_label": "RX Gain (dB)",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        },
                        "IFAmplitude": {
                            "display_label": "IF Amplitude",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        }
                    }
                })
            ],
            "groupB": [
                Configuration(config_dict={
                    "parameters": {
                        "device_name": "Device 2",
                        "device_type": "terp",
                        "prefix": 1.8,
                        "anod": 4
                    },
                    "ui_parameters": {
                        "prefix": {
                            "display_label": "Log Prefix",
                            "display_type": "text",
                            "multiplier": 1
                        },
                        "anod": {
                            "display_label": "Anodization",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        }
                    }
                })
            ],
            "groupC": [
                Configuration(config_dict={
                    "parameters": {
                        "device_name": "Device 3",
                        "device_type": "maxx",
                        "sliderA": 2,
                        "sliderB": 4,
                        "inputA": 3.5,
                        "inputB": 2.7
                    },
                    "ui_parameters": {
                        "sliderA": {
                            "display_label": "Slider A",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        },
                        "sliderB": {
                            "display_label": "Slider B",
                            "display_type": "slider",
                            "multiplier": 1,
                            "range": [1, 10],
                            "step": 1
                        },
                        "inputA": {
                            "display_label": "Text A",
                            "display_type": "text",
                            "multiplier": 2
                        },
                        "inputB": {
                            "display_label": "Text B",
                            "display_type": "text",
                            "multiplier": 2
                        }
                    }
                })
            ]
        }
import contextlib
import json
from pathlib import Path
from typing import Dict

from bioview_common import APP_VERSION
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QStyle,
    QWidget
)

from bioview_client.utils import is_dict_of_dicts, load_json_file


class ConfigurationPrompt(QDialog):
    """
    Dialog for uploading common and device configuration, loaded whenever 
    the app does not find a valid configuration
    """
    def __init__(
        self,
        experiment_config: Dict = None,
        group_configs: Dict[str, Dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.experiment_config = experiment_config or {} 

        self.group_configs = group_configs if is_dict_of_dicts(group_configs) else {}
        
        self.setup_ui()
        self.populate_device_list()
        self.update_experiment_config_preview()

    def setup_ui(self):
        self.setWindowTitle(f"Configuration - BioView Monitor {APP_VERSION}")
        self.setModal(True)
        self.resize(800, 500)
        
        layout = QVBoxLayout(self)

        # Header
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(10, 10, 10, 10) 
        
        warning_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_label = QLabel()
        icon_label.setPixmap(warning_icon.pixmap(24, 24)) 
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)

        text_label = QLabel("App was launched with incomplete configuration. Please add configuration files.")
        text_label.setWordWrap(True)

        header_layout.addWidget(icon_label)
        header_layout.addWidget(text_label, 1) # '1' lets text expand to fill space

        layout.addWidget(header_container)

        # Experiment Configuration Section
        common_group = QGroupBox("Experiment Configuration")
        common_layout = QVBoxLayout(common_group)

        update_exp_cfg_btn_layout = QHBoxLayout()
        update_exp_cfg_btn_layout.addStretch()

        self.update_experiment_cfg_btn = QPushButton("Browse Files")
        self.update_experiment_cfg_btn.clicked.connect(self.update_experiment_config)
        update_exp_cfg_btn_layout.addWidget(self.update_experiment_cfg_btn)

        self.clear_common_btn = QPushButton("Clear")
        self.clear_common_btn.clicked.connect(self.clear_experiment_config)
        self.clear_common_btn.setEnabled(False)
        update_exp_cfg_btn_layout.addWidget(self.clear_common_btn)

        common_layout.addLayout(update_exp_cfg_btn_layout)

        # Preview area for experiment config
        self.experiment_config_preview = QTextEdit()
        self.experiment_config_preview.setMaximumHeight(300) 
        common_layout.addWidget(self.experiment_config_preview)

        layout.addWidget(common_group)

        # Device Configuration Section
        device_group = QGroupBox("Device Configurations")
        device_layout = QVBoxLayout(device_group)

        device_controls_layout = QHBoxLayout()
        device_controls_layout.addWidget(QLabel("Device Configurations:"))
        device_controls_layout.addStretch()

        self.add_device_group_btn = QPushButton("Add Device Config")
        self.add_device_group_btn.clicked.connect(self.add_device_group_config)
        device_controls_layout.addWidget(self.add_device_group_btn)

        self.remove_device_group_btn = QPushButton("Remove Selected")
        self.remove_device_group_btn.clicked.connect(self.remove_device_group_config)
        self.remove_device_group_btn.setEnabled(False)
        device_controls_layout.addWidget(self.remove_device_group_btn)

        device_layout.addLayout(device_controls_layout)

        # Device config preview
        device_content_layout = QHBoxLayout()

        # Device list
        self.device_list = QListWidget()
        self.device_list.setMaximumWidth(200)
        self.device_list.itemSelectionChanged.connect(self.on_device_selection_changed)
        device_content_layout.addWidget(self.device_list)

        # Config preview
        self.device_preview = QTextEdit()
        self.device_preview.setPlaceholderText(
            "Select a device configuration to preview..."
        )
        self.device_preview.setReadOnly(True)
        device_content_layout.addWidget(self.device_preview)

        device_layout.addLayout(device_content_layout)
        layout.addWidget(device_group)

        # Dialog buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setDefault(True)
        button_layout.addWidget(self.ok_btn)

        layout.addLayout(button_layout)

    def populate_device_list(self):
        """Populate the device list with existing device configs."""
        self.device_list.clear()

        if not self.group_configs or self.group_configs == {}:
            return

        # Flatten groups and devices into the list. Store (group_id, device_id) as item data
        for group_id, group_dict in self.group_configs.items():
            # Ensure group_dict is a dict
            if not isinstance(group_dict, dict):
                group_dict = {"device_0": group_dict}
                self.group_configs[group_id] = group_dict

            for device_id, device_config in group_dict.items():
                device_name = self.get_device_display_name(device_config)
                display_label = f"{group_id}/{device_id} - {device_name}"
                item = QListWidgetItem(display_label)
                item.setData(Qt.ItemDataRole.UserRole, (group_id, device_id))
                self.device_list.addItem(item)

    def format_device_preview(self, device_config):
        """
        Format device configuration for preview, showing only valid fields.
        """
        # device_type: fields
        valid_fields = {
            "usrp": {
                "name": "Device Name",
                "type": "Device Type",
                "samp_rate": "Sampling Rate (Hz)",
                "if_freq": "IF Frequency (Hz)",
                "tx_gain": "Tx Gains (dB)",
                "rx_gain": "Rx Gains (dB)",
            },
            "biopac": {
                "name": "Device Name",
                "type": "Device Type",
                "samp_rate": "Sampling Rate (Hz)",
                "channels": "Channels",
            },
        }

        # Try to determine device type
        device_type = device_config.get("device_type", "undefined")

        if device_type not in valid_fields:
            # Just return the dictionary
            return device_config

        # Else, show in a pretty format
        # Format the preview
        formatted_lines = []
        formatted_lines.append("-" * 30)

        # Pretty format supported fields
        relevant_fields = valid_fields[device_type]
        for field in relevant_fields:  # All keys
            if field in device_config:
                value = device_config[field]
                if isinstance(value, (dict, list)):
                    value_str = json.dumps(value, indent=2)
                else:
                    value_str = str(value)
                formatted_lines.append(f"{relevant_fields[field]}: {value_str}")

        # Show any additional fields that weren't in the valid list
        other_fields = [k for k in device_config if k not in relevant_fields]
        if other_fields:
            formatted_lines.append("\nOther Parameters:")
            formatted_lines.append("-" * 15)
            for field in other_fields:
                value = device_config[field]
                if isinstance(value, (dict, list)):
                    value_str = json.dumps(value, indent=2)
                else:
                    value_str = str(value)
                formatted_lines.append(f"{field.replace('_', ' ').title()}: {value_str}")

        return "\n".join(formatted_lines)

    def update_experiment_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Add Experiment Configuration", "", "JSON Files (*.json);;All Files (*)"
        )

        # If no file is added, just return  
        if not file_path:
            return 

        try:
            # Load dict
            self.experiment_config = load_json_file(file_path)
        except ValueError as e:
            QMessageBox.critical(
                self, "Error", f"Failed to load Experiment Configuration:\n{str(e)}"
            )
        self.update_experiment_config_preview()
        self.common_status_label.setText(f"Loaded: {Path(file_path).name}")
        self.common_status_label.setStyleSheet("color: green;")
        self.clear_common_btn.setEnabled(True)

    def clear_experiment_config(self):
        self.experiment_config = {}
        self.experiment_config_preview.clear()
        self.common_status_label.setText("No Experiment Configuration loaded")
        self.common_status_label.setStyleSheet("color: gray; font-style: italic;")
        self.clear_common_btn.setEnabled(False)

    def update_experiment_config_preview(self):
        if self.experiment_config:
            preview_text = json.dumps(self.experiment_config, indent=2)
            # Truncate if too long
            if len(preview_text) > 500:
                preview_text = preview_text[:500] + "\n... (truncated)"
            self.experiment_config_preview.setPlainText(preview_text)
        else:
            self.experiment_config_preview.clear()

    def add_device_group_config(self): 
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Device Group Configuration",
            "",
            "JSON Files (*.json);;All Files (*)",
        )

        if not file_path:
            return 

        try:
            group_cfg = load_json_file(file_path)

            # Since a group name won't be provided by default, use filename stem
            # as base group id and ensure uniqueness
            stem = Path(file_path).stem or "group"
            group_id = stem
            suffix = 1
            while group_id in self.group_configs:
                group_id = f"{stem}_{suffix}"
                suffix += 1

            # Ensure that group_cfg is dict_of_dicts
            if not is_dict_of_dicts(group_cfg):
                raise ValueError(
                    "Specified device group configuration must be a dict of dict"
                )

            self.group_configs[group_id] = group_cfg

            # Refresh list and select first item in new group
            self.populate_device_list()
            for i in range(self.device_list.count()):
                item = self.device_list.item(i)
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, tuple) and data[0] == group_id:
                    self.device_list.setCurrentItem(item)
                    break

        except (
            json.JSONDecodeError,
            FileNotFoundError,
            PermissionError,
            ValueError,
        ) as e:
            QMessageBox.critical(
                self, "Error", f"Failed to load device configuration:\n{str(e)}"
            )

    def remove_device_group_config(self):
        """Remove selected device group configuration."""
        current_item = self.device_list.currentItem()

        if current_item is None:
            return

        data = current_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return

        group_id, _ = data

        # For now, we remove the entire group.
        # FUTURE: We may want to let individual device configs be changed through UI.
        with contextlib.suppress(Exception):
            del self.group_configs[group_id]

        # Refresh list
        self.populate_device_list()

        # Clear preview if no items left
        if self.device_list.count() == 0:
            self.device_preview.clear()
            self.remove_device_group_btn.setEnabled(False)

    def on_device_selection_changed(self):
        """Handle device selection change."""
        current_item = self.device_list.currentItem()
        if current_item:
            data = current_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(data, tuple) or len(data) != 2:
                self.device_preview.clear()
                return
            group_id, device_id = data
            device_config = self.group_configs.get(group_id, {}).get(device_id, {})

            # Use the formatted preview instead of raw JSON
            preview_text = self.format_device_preview(device_config)

            # Truncate if too long
            if len(preview_text) > 1000:
                preview_text = preview_text[:1000] + "\n... (truncated)"

            self.device_preview.setPlainText(preview_text)
            self.remove_device_group_btn.setEnabled(True)
        else:
            self.device_preview.clear()
            self.remove_device_group_btn.setEnabled(False)

    def get_configurations(self):
        """
        Since all configurations will be passed to UI from this function,
        we can delegate the task of wrapping in here
        """
        result = {}

        if self.experiment_config:
            result["common"] = self.experiment_config

        if self.group_configs:
            # Monitor expects device groups under key 'groups'
            result["groups"] = self.group_configs

        return result

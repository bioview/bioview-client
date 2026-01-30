import contextlib
import pprint
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

from bioview_client.utils import is_dict_of_dicts, read_experiment_config_file, read_device_config_files

class ConfigurationPrompt(QDialog):
    """
    Dialog for uploading common and device configuration, loaded whenever 
    the app does not find a valid configuration
    """
    def __init__(
        self,
        experiment_config: Dict = None,
        device_config: Dict[str, Dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.experiment_config = experiment_config or {} 
        self.device_config = device_config if is_dict_of_dicts(device_config) else {}
        
        self.setup_ui()
        self.update_device_config_preview()
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
        device_controls_layout.addStretch()

        self.add_device_config_btn = QPushButton("Add Device Configuration")
        self.add_device_config_btn.clicked.connect(self.add_device_config)
        device_controls_layout.addWidget(self.add_device_config_btn)

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

    def update_device_config_preview(self):
        # Clear preview if no items left
        if len(self.device_config) == 0:
            self.device_list.clear() 
            self.device_preview.clear()
            self.remove_device_group_btn.setEnabled(False)
        else: 
            self.remove_device_group_btn.setEnabled(True)
        
        self.device_list.clear() 
        for group_id in self.device_config:
            item = QListWidgetItem(group_id)
            item.setData(Qt.ItemDataRole.UserRole, (group_id))
            self.device_list.addItem(item)    

    def update_experiment_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Add Experiment Configuration", "", "JSON Files (*.json);;All Files (*)"
        )

        # If no file is added, just return  
        if not file_path: return 

        self.experiment_config = read_experiment_config_file(file_path)
            
        self.update_experiment_config_preview()
        self.clear_common_btn.setEnabled(True)

        QMessageBox.information(
            self, "Information", "Experiment configuration updated."
        )

    def clear_experiment_config(self):
        self.experiment_config = {}
        self.experiment_config_preview.clear()
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

    def add_device_config(self): 
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Device Group Configuration",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path: return 

        self.device_config.update(read_device_config_files(file_path))

        # Update Preview 
        self.update_device_config_preview() 

        QMessageBox.information(
            self, "Information", "Device configuration updated."
        )

    def remove_device_group_config(self):
        # This will correspond to a key in self.device_config 
        current_item = self.device_list.currentItem()
        if current_item is None: return

        # Remove from storage 
        group_id = current_item.data(Qt.ItemDataRole.UserRole)

        # Remove from UI
        current_row = self.device_list.row(current_item)
        self.device_list.takeItem(current_row)

        # For now, we remove the entire group.
        # FUTURE: We may want to let individual device configs be changed through UI.
        del self.device_config[group_id]

        # Refresh list
        self.update_device_config_preview()

    def on_device_selection_changed(self):
        current_item = self.device_list.currentItem()
        
        if current_item:
            group_id = current_item.data(Qt.ItemDataRole.UserRole)
            
            group_cfg = self.device_config[group_id] 
            preview_text = pprint.pformat(group_cfg)

            self.device_preview.setPlainText(preview_text)
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

        if self.device_config:
            # Monitor expects device groups under key 'groups'
            result["groups"] = self.device_config

        return result

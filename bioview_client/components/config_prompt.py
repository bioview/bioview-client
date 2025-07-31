import json 
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QGroupBox, QLabel, QVBoxLayout, 
    QHBoxLayout, QPushButton, QListWidget, QMessageBox, 
    QTextEdit, QFileDialog, QListWidgetItem,
)
from PyQt6.QtCore import Qt

class ConfigurationPrompt(QDialog):
    '''
    Dialog for uploading common and device configuration, loaded whenever the app does not find a valid configuration
    '''
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.common_config = {}
        self.device_configs = []
        self.setup_ui()
        
    def setup_ui(self):
        """Initialize the dialog UI."""
        self.setWindowTitle("Configuration Setup")
        self.setModal(True)
        self.resize(600, 500)
        
        layout = QVBoxLayout(self)
        
        # Header message
        header_label = QLabel("App was launched with incomplete configuration files. Please upload configuration files. Press 'Cancel' to skip.")
        header_label.setStyleSheet("color: #d32f2f; padding: 10px; background-color: #ffebee; border-radius: 4px;")
        header_label.setWordWrap(True)
        layout.addWidget(header_label)
        
        # Common Configuration Section
        common_group = QGroupBox("Common Configuration")
        common_layout = QVBoxLayout(common_group)
        
        common_info_layout = QHBoxLayout()
        self.common_status_label = QLabel("No common configuration loaded")
        self.common_status_label.setStyleSheet("color: gray; font-style: italic;")
        common_info_layout.addWidget(self.common_status_label)
        common_info_layout.addStretch()
        
        self.upload_common_btn = QPushButton("Upload Common Config")
        self.upload_common_btn.clicked.connect(self.upload_common_config)
        common_info_layout.addWidget(self.upload_common_btn)
        
        self.clear_common_btn = QPushButton("Clear")
        self.clear_common_btn.clicked.connect(self.clear_common_config)
        self.clear_common_btn.setEnabled(False)
        common_info_layout.addWidget(self.clear_common_btn)
        
        common_layout.addLayout(common_info_layout)
        
        # Preview area for common config
        self.common_preview = QTextEdit()
        self.common_preview.setMaximumHeight(100)
        self.common_preview.setPlaceholderText("Configuration Preview...")
        self.common_preview.setReadOnly(True)
        common_layout.addWidget(self.common_preview)
        
        layout.addWidget(common_group)
        
        # Device Configuration Section
        device_group = QGroupBox("Device Configurations")
        device_layout = QVBoxLayout(device_group)
        
        device_controls_layout = QHBoxLayout()
        device_controls_layout.addWidget(QLabel("Device Configurations:"))
        device_controls_layout.addStretch()
        
        self.add_device_btn = QPushButton("Add Device Config")
        self.add_device_btn.clicked.connect(self.add_device_config)
        device_controls_layout.addWidget(self.add_device_btn)
        
        self.remove_device_btn = QPushButton("Remove Selected")
        self.remove_device_btn.clicked.connect(self.remove_device_config)
        self.remove_device_btn.setEnabled(False)
        device_controls_layout.addWidget(self.remove_device_btn)
        
        device_layout.addLayout(device_controls_layout)
        
        # Device list and preview
        device_content_layout = QHBoxLayout()
        
        # Device list
        self.device_list = QListWidget()
        self.device_list.setMaximumWidth(200)
        self.device_list.itemSelectionChanged.connect(self.on_device_selection_changed)
        device_content_layout.addWidget(self.device_list)
        
        # Device preview
        self.device_preview = QTextEdit()
        self.device_preview.setPlaceholderText("Select a device configuration to preview...")
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
        
    def upload_common_config(self):
        """Upload common configuration from JSON file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Upload Common Configuration", "", "JSON Files (*.json);;All Files (*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                
                self.common_config = config_data
                self.update_common_preview()
                self.common_status_label.setText(f"Loaded: {Path(file_path).name}")
                self.common_status_label.setStyleSheet("color: green;")
                self.clear_common_btn.setEnabled(True)
                
            except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
                QMessageBox.critical(self, "Error", f"Failed to load common configuration:\n{str(e)}")
                
    def clear_common_config(self):
        """Clear common configuration."""
        self.common_config = {}
        self.common_preview.clear()
        self.common_status_label.setText("No common configuration loaded")
        self.common_status_label.setStyleSheet("color: gray; font-style: italic;")
        self.clear_common_btn.setEnabled(False)
        
    def update_common_preview(self):
        """Update common configuration preview."""
        if self.common_config:
            preview_text = json.dumps(self.common_config, indent=2)
            # Truncate if too long
            if len(preview_text) > 500:
                preview_text = preview_text[:500] + "\n... (truncated)"
            self.common_preview.setPlainText(preview_text)
        else:
            self.common_preview.clear()
            
    def add_device_config(self):
        """Add a new device configuration."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Add Device Configuration", "", "JSON Files (*.json);;All Files (*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                
                device_name = Path(file_path).stem
                # Check if device name already exists
                existing_names = [self.device_list.item(i).text() for i in range(self.device_list.count())]
                if device_name in existing_names:
                    counter = 1
                    while f"{device_name}_{counter}" in existing_names:
                        counter += 1
                    device_name = f"{device_name}_{counter}"
                
                self.device_configs.append({
                    'name': device_name,
                    'config': config_data,
                    'file_path': file_path
                })
                
                # Add to list widget
                item = QListWidgetItem(device_name)
                item.setData(Qt.ItemDataRole.UserRole, len(self.device_configs) - 1)
                self.device_list.addItem(item)
                
                # Select the newly added item
                self.device_list.setCurrentItem(item)
                
            except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
                QMessageBox.critical(self, "Error", f"Failed to load device configuration:\n{str(e)}")
                
    def remove_device_config(self):
        """Remove selected device configuration."""
        current_item = self.device_list.currentItem()
        if current_item:
            device_index = current_item.data(Qt.ItemDataRole.UserRole)
            
            # Remove from list
            self.device_configs.pop(device_index)
            self.device_list.takeItem(self.device_list.row(current_item))
            
            # Update indices for remaining items
            for i in range(self.device_list.count()):
                item = self.device_list.item(i)
                current_index = item.data(Qt.ItemDataRole.UserRole)
                if current_index > device_index:
                    item.setData(Qt.ItemDataRole.UserRole, current_index - 1)
            
            # Clear preview if no items left
            if self.device_list.count() == 0:
                self.device_preview.clear()
                self.remove_device_btn.setEnabled(False)
                
    def on_device_selection_changed(self):
        """Handle device selection change."""
        current_item = self.device_list.currentItem()
        if current_item:
            device_index = current_item.data(Qt.ItemDataRole.UserRole)
            device_config = self.device_configs[device_index]
            
            preview_text = json.dumps(device_config['config'], indent=2)
            # Truncate if too long
            if len(preview_text) > 1000:
                preview_text = preview_text[:1000] + "\n... (truncated)"
            
            self.device_preview.setPlainText(preview_text)
            self.remove_device_btn.setEnabled(True)
        else:
            self.device_preview.clear()
            self.remove_device_btn.setEnabled(False)
            
    def get_configurations(self):
        """Get all configurations as a combined dictionary."""
        result = {}
        
        if self.common_config:
            result['common'] = self.common_config
            
        if self.device_configs:
            result['devices'] = {}
            for device in self.device_configs:
                device_id = hash((device['name'], device['type']))
                result['devices'][device_id] = device['config']
                
        return result
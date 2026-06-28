import pprint

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

from bioview_common import APP_VERSION, parse_configuration_file

class ConfigurationPrompt(QDialog):
    """
    Dialog for uploading common and device configuration, loaded whenever 
    the app does not find a valid configuration
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_file = None 
        self.configurations = {} # in case we have a valid file

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"BioView Configuration Loader (Version {APP_VERSION})")
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

        text_label = QLabel("App was launched without any specifications. Consider uploading a configuration file.")
        text_label.setWordWrap(True)

        header_layout.addWidget(icon_label)
        header_layout.addWidget(text_label, stretch=1)

        layout.addWidget(header_container)

        # Configuration Preview 
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        add_cfg_file_btn = QPushButton("Add from files")
        add_cfg_file_btn.clicked.connect(self.update_config_file)
        btn_layout.addWidget(add_cfg_file_btn)

        self.remove_current_cfg_btn = QPushButton("Remove current")
        self.remove_current_cfg_btn.clicked.connect(self.remove_config_file)
        self.remove_current_cfg_btn.setEnabled(False)
        btn_layout.addWidget(self.remove_current_cfg_btn)

        layout.addLayout(btn_layout)

        # Preview area
        preview_group = QGroupBox("Preview")
        preview_layout = QHBoxLayout(preview_group)

        self.cfg_items_list = QListWidget()
        self.cfg_items_list.setMaximumWidth(200)
        self.cfg_items_list.setMaximumHeight(500) 
        self.cfg_items_list.itemSelectionChanged.connect(self.on_selection_changed)
        preview_layout.addWidget(self.cfg_items_list)

        self.preview_text_area = QTextEdit()
        self.preview_text_area.setMaximumHeight(500) 
        self.preview_text_area.setReadOnly(True)
        preview_layout.addWidget(self.preview_text_area)

        layout.addWidget(preview_group)

        # Bottom buttons (Ok/Cancel)
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        footer_layout.addWidget(ok_btn)

        layout.addLayout(footer_layout)

    def update_preview(self):
        # Clear preview if no items left
        if len(self.configurations) == 0:
            self.cfg_items_list.clear() 
            self.preview_text_area.clear()
            self.remove_current_cfg_btn.setEnabled(False)
        
        if self.config_file: 
            self.remove_current_cfg_btn.setEnabled(True)
        else: 
            self.remove_current_cfg_btn.setEnabled(False)
        
        self.cfg_items_list.clear() 
        for group_id in self.configurations:
            item = QListWidgetItem(group_id)
            item.setData(Qt.ItemDataRole.UserRole, (group_id))
            self.cfg_items_list.addItem(item)    

    def update_config_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Add configuration file", "", 
            "BioView Files (*.bview), JSON Files (*.json);;All Files (*)"
        )

        # If no file is added, just return  
        if not file_path: return 

        self.config_file = file_path 
        self.configurations = parse_configuration_file(self.config_file)
        self.update_preview()
        
        self.remove_current_cfg_btn.setEnabled(True)

        QMessageBox.information(
            self, "Information", "Configuration successfully updated!"
        )

    def remove_config_file(self):
        self.config_file = None 
        self.configurations = {} 
        self.update_preview() 

    def on_selection_changed(self):
        current_item = self.cfg_items_list.currentItem()
        
        if current_item:
            group_id = current_item.data(Qt.ItemDataRole.UserRole)
            
            group_cfg = self.configurations[group_id]
            # The stored value is a configuration object, not a dict. Render the
            # underlying parameter dict so the preview is human-readable instead
            # of "<... Configuration object at 0x...>".
            cfg_dict = group_cfg.to_dict() if hasattr(group_cfg, "to_dict") else group_cfg
            preview_text = pprint.pformat(cfg_dict, sort_dicts=False)

            self.preview_text_area.setPlainText(preview_text)
        else:
            self.preview_text_area.clear()

    def get_config_file(self): 
        return self.config_file
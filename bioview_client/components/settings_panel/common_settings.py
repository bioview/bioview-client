import qtawesome as qta
from bioview_common import Configuration, DataSource, DeviceStatus
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
)

from bioview_client.components.common import CheckableComboBox
from bioview_client.constants import get_qcolor


class CommonSettingsPanel(QGroupBox):
    parameter_changed = pyqtSignal(str, object)
    log_event = pyqtSignal(str, str)
    display_duration_changed = pyqtSignal(int)
    grid_layout_changed = pyqtSignal(int, int)
    add_data_source = pyqtSignal(DataSource)
    remove_data_source = pyqtSignal(DataSource)

    def __init__(self, config: Configuration, parent=None):
        super().__init__("Experiment Settings", parent)

        self.file_name = config.get_param("file_name", "")
        self.save_dir = config.get_param("save_dir", "")

        self.param_inputs = {}
        self.init_ui()

    def get_emittable_signals(self):
        return {
            'parameter_changed': self.parameter_changed,
            'display_duration_changed': self.display_duration_changed, 
            'grid_layout_changed': self.grid_layout_changed, 
            'add_data_source': self.add_data_source, 
            'remove_data_source': self.remove_data_source 
        }
    
    def init_ui(self):
        layout = QGridLayout()
        row = 0

        # File Name Text Box
        layout.addWidget(QLabel("File Name"), row, 0)
        self.file_name_textbox = QLineEdit()
        self.file_name_textbox.setText(self.file_name)
        self.file_name_textbox.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.file_name_textbox.textChanged.connect(
            lambda value: self.update_param(param_name="file_name", value=value)
        )
        layout.addWidget(self.file_name_textbox, row, 1)
        row += 1

        # Folder picker
        layout.addWidget(QLabel("Save Path"), row, 0)

        picker_layout = QHBoxLayout()
        self.save_path_textbox = QLineEdit()
        self.save_path_textbox.setReadOnly(True)  # Make it read-only
        self.save_path_textbox.setText(self.save_dir)
        # Update folder_path as it changes
        self.save_path_textbox.textChanged.connect(
            lambda value: self.update_param(param_name="save_dir", value=value)
        )
        picker_layout.addWidget(self.save_path_textbox)

        # Button to trigger folder selection dialog
        self.browse_button = QPushButton("  Browse")
        self.browse_button.setIcon(qta.icon("fa6s.folder", color=get_qcolor("mint")))
        self.browse_button.clicked.connect(self.openFolderDialog)
        picker_layout.addWidget(self.browse_button)

        layout.addLayout(picker_layout, row, 1)
        row += 1

        # Display Time Length
        layout.addWidget(QLabel("Display Time (s)"), row, 0)
        time_layout = QHBoxLayout()
        self.time_input = QSpinBox()
        self.time_input.setRange(1, 30)
        self.time_input.setValue(10)  # Set initial value
        self.time_input.valueChanged.connect(self.update_display_time)
        time_layout.addWidget(self.time_input)
        layout.addLayout(time_layout, row, 1)
        row += 1

        # Grid Design
        layout.addWidget(QLabel("Plot Layout"), row, 0)

        grid_layout = QHBoxLayout()
        self.rows_input = QSpinBox()
        self.rows_input.setRange(1, 4)
        self.rows_input.setValue(2)
        self.rows_input.valueChanged.connect(self.update_grid)
        self.rows_input.setEnabled(True)
        grid_layout.addWidget(self.rows_input)

        self.cols_input = QSpinBox()
        self.cols_input.setRange(1, 3)
        self.cols_input.setValue(2)
        self.cols_input.valueChanged.connect(self.update_grid)
        self.cols_input.setEnabled(True)
        grid_layout.addWidget(self.cols_input)

        layout.addLayout(grid_layout, row, 1)
        row += 1

        # Channel selection
        layout.addWidget(QLabel("Plot Sources"), row, 0)
        self.plot_source = CheckableComboBox()

        # Assuming available_channels cntains DataSource objects
        # for source in self.data_sources:
        #     self.plot_source.addItem(source)

        self.plot_source.selectionChanged.connect(self.request_channel_update)

        layout.addWidget(self.plot_source, row, 1)
        self.setLayout(layout)

    # Handle theme changes
    def _update_icons(self):
        self.browse_button.setIcon(qta.icon("fa6s.folder", color=get_qcolor("mint")))

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def update_display_time(self):
        self.display_duration_changed.emit(self.time_input.value())

    def update_grid(self):
        self.grid_layout_changed.emit(self.rows_input.value(), self.cols_input.value())

    def request_channel_update(self, action: str, source: DataSource):
        """Handle channel selection changes"""
        if action == "remove":
            self.remove_data_source.emit(source)
        elif action == "add":
            self.add_data_source.emit(source)
        else:
            return

    def update_source(self, action: str, source: DataSource):
        """Update channel selection state"""
        if action == "add":
            self.plot_source.select_source(source)
        elif action == "remove":
            self.plot_source.unselect_source(source)
        else:
            return None

    def set_available_sources(self, sources):
        """Populate the plot-source selector with the available data sources."""
        self.plot_source.set_sources(sources)

    def openFolderDialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            # If user didn't cancel the dialog
            self.save_path_textbox.setText(folder)

    def update_param(self, param_name, value):
        self.parameter_changed.emit(param_name, value)

    def update_button_states(self, device_status):
        if device_status == DeviceStatus.STREAMING:
            # Do not allow grid updates during streaming
            self.rows_input.setEnabled(False)
            self.cols_input.setEnabled(False)
            # Do not allow changing save file path mid-streaming to avoid data loss
            self.file_name_textbox.setEnabled(False)
            self.save_path_textbox.setEnabled(False)
            self.browse_button.setEnabled(False)
        else:
            self.rows_input.setEnabled(True)
            self.cols_input.setEnabled(True)
            self.file_name_textbox.setEnabled(True)
            self.save_path_textbox.setEnabled(True)
            self.browse_button.setEnabled(True)

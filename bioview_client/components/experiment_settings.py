import qtawesome as qta
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSpinBox,
)

from bioview_client.constants import (
    ConnectionStatus,
    DataSource,
    RunningStatus,
    get_qcolor
)

class CheckableListView(QListView):
    def __init__(self, combo_box):
        super().__init__(combo_box)
        self.combo_box = combo_box

    def mouseReleaseEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            item = self.model().itemFromIndex(index)
            self.combo_box.toggle_item(item)
            self.viewport().update()
            self.combo_box.showPopup()
        else:
            super().mouseReleaseEvent(event)


class CheckableComboBox(QComboBox):
    selectionChanged = pyqtSignal(str, DataSource)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view = CheckableListView(self)
        self.setView(self._view)
        self.setModel(QStandardItemModel(self))
        self.view().viewport().installEventFilter(self)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("Select options...")
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.clearEditText()

    def addItem(self, source: DataSource, checked=False):
        """Add a DataSource item to the combo box"""
        text = source.label
        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        # Store the DataSource object in the item's UserRole
        item.setData(source, Qt.ItemDataRole.UserRole)
        self.model().appendRow(item)
        if checked:
            self.selectionChanged.emit("add", source)

    def toggle_item(self, item: QStandardItem):
        """Toggle the check state of an item and emit the appropriate signal"""
        source = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            item.setCheckState(Qt.CheckState.Unchecked)
            self.selectionChanged.emit("remove", source)
        else:
            item.setCheckState(Qt.CheckState.Checked)
            self.selectionChanged.emit("add", source)
        self.update_line_text()

    def select_source(self, source: DataSource):
        for i in range(self.model().rowCount()):
            item = self.model().item(i)
            item_source = item.data(Qt.ItemDataRole.UserRole)
            if item_source == source and item.checkState() != Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Checked)
                self.update_line_text()
                break

    def unselect_source(self, source: DataSource):
        for i in range(self.model().rowCount()):
            item = self.model().item(i)
            item_source = item.data(Qt.ItemDataRole.UserRole)
            if item_source == source and item.checkState() != Qt.CheckState.Unchecked:
                item.setCheckState(Qt.CheckState.Unchecked)
                self.update_line_text()
                break

    def checkedItems(self):
        """Return list of checked DataSource objects"""
        checked_sources = []
        for i in range(self.model().rowCount()):
            item = self.model().item(i)
            if item.checkState() == Qt.CheckState.Checked:
                source = item.data(Qt.ItemDataRole.UserRole)
                checked_sources.append(source)
        return checked_sources

    def checkedItemTexts(self):
        """Return list of checked source names"""
        return [
            self.model().item(i).text()
            for i in range(self.model().rowCount())
            if self.model().item(i).checkState() == Qt.CheckState.Checked
        ]

    def update_line_text(self):
        """Update the line edit text with checked source names"""
        checked_texts = self.checkedItemTexts()
        self.lineEdit().setText(", ".join(checked_texts) if checked_texts else "")

    def eventFilter(self, source, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            index = self.view().indexAt(event.pos())
            if not index.isValid():
                self.hidePopup()
        return super().eventFilter(source, event)


class ExperimentSettingsPanel(QGroupBox):
    parameterChanged = pyqtSignal(str, object)
    logEvent = pyqtSignal(str, str)
    timeWindowChanged = pyqtSignal(int)
    gridLayoutChanged = pyqtSignal(int, int)
    addChannelRequested = pyqtSignal(object)  # Changed to emit DataSource object
    removeChannelRequested = pyqtSignal(object)  # Changed to emit DataSource object

    def __init__(self, config, parent=None):
        super().__init__("Experiment Settings", parent)
        self.config = config
        self.param_inputs = {}
        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()
        row = 0

        # Create parameter inputs
        file_name = self.config.get("file_name")
        save_dir = self.config.get("save_dir")

        # File Name
        layout.addWidget(QLabel("File Name"), row, 0)
        self.file_name = QLineEdit()
        self.file_name.setText(file_name)
        self.file_name.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.file_name.textChanged.connect(
            lambda value: self.update_param(param_name="file_name", value=value)
        )
        layout.addWidget(self.file_name, row, 1)
        row += 1

        # Folder picker
        layout.addWidget(QLabel("Save Path"), row, 0)

        picker_layout = QHBoxLayout()
        self.folder_path = QLineEdit()
        self.folder_path.setReadOnly(True)  # Make it read-only
        self.folder_path.setText(save_dir)
        # Update folder_path as it changes
        self.folder_path.textChanged.connect(
            lambda value: self.update_param(param_name="save_dir", value=value)
        )
        picker_layout.addWidget(self.folder_path)

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
        # Assuming available_channels contains DataSource objects
        for source in self.config.get('available_channels', []):
            self.plot_source.addItem(source)
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
        self.timeWindowChanged.emit(self.time_input.value())

    def update_grid(self):
        self.gridLayoutChanged.emit(self.rows_input.value(), self.cols_input.value())

    def request_channel_update(self, action: str, source: DataSource):
        """Handle channel selection changes"""
        if action == "remove":
            self.removeChannelRequested.emit(source)
        elif action == "add":
            self.addChannelRequested.emit(source)
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

    def openFolderDialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            # If user didn't cancel the dialog
            self.folder_path.setText(folder)

    def update_param(self, param_name, value):
        self.config[param_name] = value
        # TODO: Add callback to propagate this 

    def update_button_states(self, connection_status, running_status):
        if connection_status == ConnectionStatus.CONNECTED:
            self.rows_input.setEnabled(running_status == RunningStatus.STOPPED)
            self.cols_input.setEnabled(running_status == RunningStatus.STOPPED)
            self.file_name.setEnabled(running_status == RunningStatus.STOPPED)
            self.folder_path.setEnabled(running_status == RunningStatus.STOPPED)
            self.browse_button.setEnabled(running_status == RunningStatus.STOPPED)
        else:
            self.rows_input.setEnabled(True)
            self.cols_input.setEnabled(True)

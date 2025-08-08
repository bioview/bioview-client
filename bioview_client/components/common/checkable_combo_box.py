from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QComboBox, QListView

# TODO: Remove DataSource as a dependency
from bioview_common import DataSource

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
from PyQt6.QtWidgets import QStatusBar, QLabel, QPushButton, QWidget, QHBoxLayout

class StatusBar(QStatusBar):
    def __init__(self, parent = ...):
        super().__init__(parent) 

        # Example: Add a custom label and button
        self.label = QLabel("Ready")
        self.button = QPushButton("Info")
        self.button.clicked.connect(self.show_info)

        # Use a QWidget with a layout to group widgets
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.button)
        container.setLayout(layout)

        self.addPermanentWidget(container)

    def show_info(self):
        self.label.setText("Button clicked!")
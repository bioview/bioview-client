import socket
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QPushButton, QComboBox, QStatusBar, QLabel, QWidget)
from PyQt6.QtCore import pyqtSignal, QEvent, Qt

from bioview_common import DeviceStatus

class ServerConnector(QWidget):
    '''
    GUI Component which displays available/connected servers and provides for an option to swap out among servers. 
    Server connection state is emitted back to the main app for further co-ordination.
    '''
    data_received = pyqtSignal(dict)
    connection_status_changed = pyqtSignal(str)
    status_message = pyqtSignal(str)
    scan_progress = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # Track servers 
        # TODO: Localhost should always be attempted. Check if a bioview-server is available 
        self.selected_server = 'localhost'
        self.available_servers = {}

        # Setup UI
        self.init_ui()
        
        # Setup network scanner
        self.network_scanner = NetworkScanner()
        self.network_scanner.server_found.connect(self.on_server_found)
        self.network_scanner.scan_progress.connect(self.update_scan_progress)
        self.network_scanner.scan_complete.connect(self.on_scan_complete)
        
        # Show local network info
        self.local_ip, self.network_ip, self.hostname = self.get_local_network_info()
        if self.local_ip:
            self.emit_status(f"Local IP: {self.local_ip} | Network: {self.network_ip} | Hostname: {self.hostname}")
        else:
            self.emit_status("Ready - Enter server address or scan network")
    
    def init_ui(self):
        """Setup the user interface"""
        # Control panel
        control_layout = QHBoxLayout(self)
        
        # Scan button
        self.scan_btn = QPushButton("Scan Network")
        self.scan_btn.clicked.connect(self.scan_network)
        control_layout.addWidget(self.scan_btn)
        
        # Server dropdown (populated by scan)
        self.server_dropdown = QComboBox()
        
        self.server_dropdown.addItem(self.selected_server)
        self.server_dropdown.setEnabled(False)
        self.server_dropdown.currentTextChanged.connect(self.on_server_selected)
        control_layout.addWidget(self.server_dropdown)

        # Connect button
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_server)
        self.connect_btn.setEnabled(False)
        control_layout.addWidget(self.connect_btn)
        
        # Disconnect button
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_from_server)
        self.disconnect_btn.setEnabled(False)
        control_layout.addWidget(self.disconnect_btn)

        self.setLayout(control_layout)
    
    def scan_network(self):
        """Start network scan for servers"""
        self.scan_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        
        self.available_servers = {} 

        self.server_dropdown.clear()
        self.server_dropdown.setEnabled(False)
        
        # Start scanning
        self.network_scanner.start()
    
    def on_server_found(self, hostname, ip, data_port, control_port):
        # This may happen during a scan so we do not update any text. 
        self.server_dropdown.addItem(hostname)
        self.available_servers[hostname] = {
            'ip': ip,
            'data_port': data_port,
            'control_port': control_port
        }
    
    def update_scan_progress(self, progress):
        """Emit scan progress to main app for status"""
        self.scan_progress.emit(progress)
    
    def on_scan_complete(self):
        """Handle scan completion"""
        self.scan_btn.setEnabled(True)
        
        if self.server_dropdown.count() == 0:
            self.server_dropdown.addItem("No servers available")
            self.server_dropdown.setEnabled(False)
            self.emit_status("Scan complete: No servers found. Confirm that local server is running.")
        else:
            # TODO: Handle button updates using states and callbacks
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.emit_status(f"Scan complete: Select servers from dropdown.")
    
    def on_server_selected(self, server_name):
        """Handle server selection from dropdown"""
        self.selected_server = str(server_name)
    
    def connect_to_server(self):
        """Connect to the data server"""
        # Parse server input
        server_name = self.selected_server # This will now just be hostname
        if not server_name:
            self.emit_status("Invalid server selected.")
            return
        
        try:
            server_dict = self.available_servers[server_name]
            host = server_dict['ip']
            data_port = server_dict['data_port']
            control_port = server_dict['control_port']

        except ValueError:
            self.emit_status("Invalid server address format")
            return
        
        # Initialize connection
        self.data_receiver.set_server_address(host, data_port, control_port)
        self.data_receiver.start_receiving()

        # Update UI
        self.connect_btn.setEnabled(False) # re-enable on failed connection
        self.disconnect_btn.setEnabled(False) # re-enable on successful connection
        
        self.emit_status(f"Connecting to {server_name}")
    
    def disconnect_from_server(self):
        """Disconnect from the data server"""
        self.data_receiver.stop_receiving()
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.emit_status("Disconnected")
    
    def get_local_network_info(self):
        """Get local network information for display"""
        try:
            # Get local IP
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            sock.close()
            
            # Extract network
            ip_parts = local_ip.split('.')
            network = '.'.join(ip_parts[:3]) + '.0/24'
            
            # Get hostname 
            hostname = socket.gethostname()

            return local_ip, network, hostname
        except Exception:
            return None, None, None 
    
    def update_connection_status(self, status):
        """Update connection status display"""
        self.connection_label.setText(f"Status: {status}")
        if status == "Connected":
            self.connection_label.setStyleSheet("color: green")
        elif status == "Disconnected":
            self.connection_label.setStyleSheet("color: red")
        else:
            self.connection_label.setStyleSheet("color: orange")
        
        # Emit signal for parent application
        self.connection_status_changed.emit(status)
    
    def on_data_received(self, data):
        """Handle received physiological data"""
        # Forward data to parent application
        self.data_received.emit(data)
    
    def emit_status(self, message):
        """Emit status message for parent application"""
        self.status_message.emit(message)
    
    def cleanup(self):
        """Clean up resources when component is being destroyed"""
        if self.network_scanner.isRunning():
            self.network_scanner.stop_scan()
            self.network_scanner.wait()
        
        self.disconnect_from_server()

    def closeEvent(self, event):
        """Handle widget close"""
        self.cleanup()
        super().closeEvent(event)

class StatusIndicator(QWidget):
    """Indicate device status using the following codes -
    Connected: Green ,
    Connecting: Yellow,
    Disconnected: Red
    """

    def __init__(self, state=DeviceStatus.DISCONNECTED, size: int = 12):
        super().__init__()
        self.state = state
        self.size = size
        self.setFixedSize(size, size)

        self.update_state(state)

    def update_state(self, state):
        self.state = state
        self.repaint()

    def paintEvent(self, event):
        # Draw the LED circle with appropriate color
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = self.state.value[1]

        painter.setBrush(color)
        painter.setPen(QPen(QColor(50, 50, 50), 1))

        margin = 1
        painter.drawEllipse(
            margin, margin, self.size - 2 * margin, self.size - 2 * margin
        )

class DeviceStatusWidget(QWidget):
    def __init__(self, device_name, device_state=DeviceStatus.DISCONNECTED):
        super().__init__()
        self.device_name = device_name
        self.device_state = device_state

        # Create horizontal layout
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(5)

        self.label = QLabel(device_name)
        self.indicator = StatusIndicator(device_state)

        # Add widgets to layout
        layout.addWidget(self.label)
        layout.addWidget(self.indicator)

        self.setLayout(layout)

    def update_state(self, new_state):
        self.device_state = new_state
        self.indicator.update_state(new_state)


class DeviceStatusPanel(QWidget):
    def __init__(self, devices):
        super().__init__()
        self.devices = devices.copy()
        self.device_widgets = {}

        # Create horizontal layout for all devices
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(15)
        
        # Add device widgets
        for device_name, device_map in self.devices.items():
            device_state = device_map["state"]
            self.add_device(device_name, device_state)

        self.setLayout(self.layout)
    
    # Handle theme changes
    def _update_icons(self):
        for device_name, device_state in self.devices.items():
            self.device_widgets[device_name].update_state(device_state)

    def event(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self._update_icons()
        return super().event(event)

    def add_device(self, device_name, device_state=DeviceStatus.DISCONNECTED):
        device_widget = DeviceStatusWidget(device_name, device_state)
        self.device_widgets[device_name] = device_widget
        self.layout.addWidget(device_widget)
        self.devices[device_name] = device_state

    def update_device_state(self, device_name, new_state):
        if device_name in self.device_widgets.keys():
            self.device_widgets[device_name].update_state(new_state)
            self.devices[device_name] = new_state

    def remove_device(self, device_name):
        if device_name in self.device_widgets:
            widget = self.device_widgets[device_name]
            self.layout.removeWidget(widget)
            widget.deleteLater()
            del self.device_widgets[device_name]
            del self.devices[device_name]

class StatusBar(QStatusBar):
    def __init__(self, parent = ...):
        super().__init__(parent) 

        # Use a QWidget with a layout to group widgets
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(ServerConnector(), alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        layout.addWidget(DeviceStatusPanel(devices={}), alignment=Qt.AlignmentFlag.AlignRight)
        container.setLayout(layout)

        self.addPermanentWidget(container, stretch=1)

    def show_info(self):
        self.label.setText("Button clicked!")
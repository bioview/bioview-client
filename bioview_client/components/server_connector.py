import socket
import json
import threading
import numpy as np
from collections import deque
from PyQt6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                           QGroupBox, QGridLayout, QLineEdit,
                           QComboBox, QProgressBar)
from PyQt6.QtCore import pyqtSignal, QObject, QThread

class NetworkScanner(QThread):
    """Scan local network for physiological data servers"""
    server_found = pyqtSignal(str, int)
    scan_progress = pyqtSignal(int)
    scan_complete = pyqtSignal()
    
    def __init__(self, port=8888):
        super().__init__()
        self.port = port
        self.scanning = False
        
    def run(self):
        """Scan local network for servers"""
        self.scanning = True
        
        # Get local IP range
        local_ip = self.get_local_ip()
        if not local_ip:
            self.scan_complete.emit()
            return
            
        # Extract network prefix (assumes /24 subnet)
        ip_parts = local_ip.split('.')
        network_prefix = '.'.join(ip_parts[:3])
        
        # Scan IP range
        for i in range(1, 255):
            if not self.scanning:
                break
                
            target_ip = f"{network_prefix}.{i}"
            
            try:
                # Quick connection test
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.1)  # 100ms timeout
                result = sock.connect_ex((target_ip, self.port))
                
                if result == 0:
                    # Server found
                    self.server_found.emit(target_ip, self.port)
                
                sock.close()
                
            except Exception:
                pass
            
            # Update progress
            progress = int((i / 254) * 100)
            self.scan_progress.emit(progress)
        
        self.scan_complete.emit()
    
    def get_local_ip(self):
        """Get the local IP address"""
        try:
            # Connect to a remote address to determine local IP
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            sock.close()
            return local_ip
        except Exception:
            return None
    
    def stop_scan(self):
        """Stop the network scan"""
        self.scanning = False

class DataReceiver(QObject):
    """Thread-safe data receiver from server"""
    data_received = pyqtSignal(dict)
    connection_status = pyqtSignal(str)
    
    def __init__(self, host='192.168.1.100', port=8888):  # Default to common local IP
        super().__init__()
        self.host = host
        self.port = port
        self.socket = None
        self.running = False
        self.thread = None
        
    def set_server_address(self, host, port=8888):
        """Update server address"""
        self.host = host
        self.port = port
        
    def start_receiving(self):
        """Start receiving data in a separate thread"""
        self.running = True
        self.thread = threading.Thread(target=self._receive_data)
        self.thread.daemon = True
        self.thread.start()
        
    def stop_receiving(self):
        """Stop receiving data"""
        self.running = False
        if self.socket:
            self.socket.close()
        if self.thread:
            self.thread.join(timeout=1.0)
    
    def _receive_data(self):
        """Receive data from server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connection_status.emit("Connected")
            
            buffer = ""
            while self.running:
                try:
                    data = self.socket.recv(1024).decode('utf-8')
                    if not data:
                        break
                    
                    buffer += data
                    lines = buffer.split('\n')
                    buffer = lines[-1]  # Keep incomplete line
                    
                    for line in lines[:-1]:
                        if line.strip():
                            try:
                                sample = json.loads(line)
                                self.data_received.emit(sample)
                            except json.JSONDecodeError:
                                continue
                                
                except socket.error:
                    break
                    
        except Exception as e:
            self.connection_status.emit(f"Connection error: {e}")
        finally:
            self.connection_status.emit("Disconnected")
            if self.socket:
                self.socket.close()

class ServerConnector(QGroupBox):
    # Signals that parent applications can connect to
    data_received = pyqtSignal(dict)
    connection_status_changed = pyqtSignal(str)
    status_message = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Setup UI
        self.init_ui()
        
        # Setup data receiver
        self.data_receiver = DataReceiver()
        self.data_receiver.connection_status.connect(self.update_connection_status)
        self.data_receiver.data_received.connect(self.on_data_received)
        
        # Setup network scanner
        self.network_scanner = NetworkScanner()
        self.network_scanner.server_found.connect(self.on_server_found)
        self.network_scanner.scan_progress.connect(self.update_scan_progress)
        self.network_scanner.scan_complete.connect(self.on_scan_complete)
        
        # Show local network info
        local_ip, network = self.get_local_network_info()
        if local_ip:
            self.emit_status(f"Local IP: {local_ip} | Network: {network}")
        else:
            self.emit_status("Ready - Enter server address or scan network")
    
    def init_ui(self):
        """Setup the user interface"""
        # Main layout
        main_layout = QVBoxLayout(self)
        
        # Control panel
        control_layout = QHBoxLayout()
        # Server connection section
        server_label = QLabel("Server:")
        control_layout.addWidget(server_label)
        
        self.server_input = QLineEdit("192.168.1.100:8888") # TODO: Replace with local IP
        self.server_input.setPlaceholderText("Enter server IP:port")
        control_layout.addWidget(self.server_input)
        
        # Connect button
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_server)
        control_layout.addWidget(self.connect_btn)
        
        # Disconnect button
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_from_server)
        control_layout.addWidget(self.disconnect_btn)

        # Scan button
        self.scan_btn = QPushButton("Scan Network")
        self.scan_btn.clicked.connect(self.scan_network)
        control_layout.addWidget(self.scan_btn)
        
        # Server dropdown (populated by scan)
        self.server_dropdown = QComboBox()
        self.server_dropdown.addItem("No servers found")
        self.server_dropdown.currentTextChanged.connect(self.on_server_selected)
        control_layout.addWidget(self.server_dropdown)
        
        main_layout.addLayout(control_layout)
        
        # Network info label
        network_layout = QHBoxLayout()
        self.network_info_label = QLabel()
        network_layout.addWidget(self.network_info_label)
        # Status labels
        self.connection_label = QLabel("Status: Disconnected")
        network_layout.addWidget(self.connection_label)
        # Progress bar for scanning
        self.scan_progress = QProgressBar()
        self.scan_progress.setVisible(False)
        network_layout.addWidget(self.scan_progress)
        
        main_layout.addLayout(network_layout)
    
    def scan_network(self):
        """Start network scan for servers"""
        self.scan_btn.setEnabled(False)
        self.scan_progress.setVisible(True)
        self.scan_progress.setValue(0)
        self.server_dropdown.clear()
        self.server_dropdown.addItem("Scanning...")
        
        # Start scanning
        self.network_scanner.start()
    
    def on_server_found(self, ip, port):
        """Handle server discovery"""
        server_text = f"{ip}:{port}"
        # Remove "Scanning..." if it's the first server found
        if self.server_dropdown.count() == 1 and self.server_dropdown.itemText(0) == "Scanning...":
            self.server_dropdown.clear()
        
        self.server_dropdown.addItem(server_text)
        self.emit_status(f"Found server at {server_text}")
    
    def update_scan_progress(self, progress):
        """Update scan progress bar"""
        self.scan_progress.setValue(progress)
    
    def on_scan_complete(self):
        """Handle scan completion"""
        self.scan_btn.setEnabled(True)
        self.scan_progress.setVisible(False)
        
        if self.server_dropdown.count() == 0:
            self.server_dropdown.addItem("No servers found")
            self.emit_status("No servers found on network")
        else:
            self.emit_status(f"Scan complete - found {self.server_dropdown.count()} server(s)")
    
    def on_server_selected(self, server_text):
        """Handle server selection from dropdown"""
        if server_text and ":" in server_text and server_text != "No servers found":
            self.server_input.setText(server_text)
    
    def connect_to_server(self):
        """Connect to the data server"""
        # Parse server input
        server_text = self.server_input.text().strip()
        if not server_text:
            self.emit_status("Please enter server address")
            return
        
        try:
            if ":" in server_text:
                host, port = server_text.split(":", 1)
                port = int(port)
            else:
                host = server_text
                port = 8888
        except ValueError:
            self.emit_status("Invalid server address format")
            return
        
        # Update data receiver with new server address
        self.data_receiver.set_server_address(host, port)
        
        # Start connection
        self.data_receiver.start_receiving()
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.start_time = None
        self.sample_count = 0
        self.buffer_ready = False
        self.buffer_start_time = None
        
        self.emit_status(f"Connecting to {host}:{port}...")
    
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
            
            return local_ip, network
        except Exception:
            return None, None
    
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
        self.network_info_label.setText(message)
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
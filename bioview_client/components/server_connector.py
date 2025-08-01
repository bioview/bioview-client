import socket
from PyQt6.QtWidgets import (QHBoxLayout, QPushButton, QGroupBox, QComboBox)
from PyQt6.QtCore import pyqtSignal, QThread

class NetworkScanner(QThread):
    scan_progress = pyqtSignal(int)
    scan_complete = pyqtSignal()
    server_found = pyqtSignal(str, int)
    
    def __init__(self, data_port=8888, control_port=8889):
        super().__init__()
        # Available servers should have both data and control port
        self.data_port = data_port
        self.control_port = control_port
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
                
                # NOTE: This may be potentially vulnerable. However, since this is being done over a local network itself, the risks may be low. 
                # TODO: Confirm security with someone who deals with a networking stack. 

                # We will only try connecting to control ports
                result = sock.connect_ex((target_ip, self.control_port))
                
                if result == 0:
                    # Server found
                    self.server_found.emit(target_ip, self.data_port, self.control_port)
                
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

class ServerConnector(QGroupBox):
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
        
        # Setup UI
        self.init_ui()
        
        # Setup network scanner
        self.network_scanner = NetworkScanner()
        self.available_servers = {}
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
        self.server_dropdown.addItem("No servers found")
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
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(True)

        self.scan_progress.setVisible(False)
        
        if self.server_dropdown.count() == 0:
            self.server_dropdown.addItem("No servers available")
            self.server_dropdown.setEnabled(False)
            self.emit_status("Scan complete: No servers found. Confirm that local server is running.")
        else:
            self.emit_status(f"Scan complete: Select servers from dropdown.")
    
    def on_server_selected(self, server_text):
        """Handle server selection from dropdown"""
        if self.server_dropdown.count() != 0: 
            self.server_input.setText(server_text)
    
    def connect_to_server(self):
        """Connect to the data server"""
        # Parse server input
        server_name = self.server_input.text() # This will now just be hostname
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
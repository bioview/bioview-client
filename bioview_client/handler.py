''' Client-side handler 

The client side handler connects to available servers (which may be remote), and 
wraps communication to/from the server to provide to any suitable frontend that uses
the client handler. The goal of this handler is to be front-end agnostic and provides
the following functionality - 
1. Server Connection Ping: This checks whether a server is available to be connected to
2. Device Discovery: Using the server's discovery functionality, provides the client
                    with all available device backends
3. Device Connection: Initiates connection with the device to get them ready to stream
4. Streaming: Starts streaming from backend devices with display buffers sent to client 
                    for graphical output (if requested)
5. Device Configuration: Allows device configuration to be modified from the client side 

By default, the client operates on localhost at ports 9999 (control) and 9998 (data). 
This can be modified for remote operation.
'''

import time 
import json
import struct # TODO: Remove by confirming packet structure
import socket 
import numpy as np

from PyQt6.QtCore import QThread, pyqtSignal

from bioview_client.constants import Command, Response, MAX_BUFFER_SIZE

SUPPORTED_COMMANDS = [
    Command.PING,
    Command.DISCOVER,
    Command.CONNECT,
    Command.DISCONNECT,
    Command.CONFIGURE,
    Command.START,
    Command.STOP,
    Command.UPDATE
]

class Client(QThread):
    # Control signals that provide the functionality listed above
    server_connected = pyqtSignal()
    server_disconnected = pyqtSignal()
    device_connected = pyqtSignal(str)
    device_connection_failed = pyqtSignal(str)
    device_disconnected = pyqtSignal()
    streaming_started = pyqtSignal()
    streaming_stopped = pyqtSignal()
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str, str)
    
    # Data signals for graphical output
    data_received = pyqtSignal(np.ndarray) 
    
    def __init__(self, address='localhost', data_port=9998, control_port=9999):
        super().__init__()
        
        # Connection parameters
        self.address = address
        self.data_port = data_port
        self.control_port = control_port
        
        # Sockets
        self.control_socket = None
        self.data_socket = None
        
        # State
        self.control_connected = False
        self.data_connected = False
        self.running = False
        
        # Data streaming
        self.streaming_active = False
        
    def start_client(self):
        """Start the client worker"""
        self.running = True
        self.start()
    
    def stop_client(self):
        """Stop the client worker"""
        self.running = False
        self.disconnect_from_server()
        self.quit()
        self.wait()
    
    def run(self):
        """Main client thread"""
        self.log_message.emit("info", "Streaming client worker started")
        
        while self.running:
            # Try to maintain control connection
            if not self.control_connected:
                if self.connect_control():
                    self.server_connected.emit()
                else:
                    time.sleep(2)
                    continue
            
            # If streaming is active, maintain data connection
            if self.streaming_active and not self.data_connected:
                self.connect_data()
            
            time.sleep(0.1)
    
    def connect_control(self):
        """Connect to control server"""
        try:
            if self.control_socket:
                self.control_socket.close()
            
            self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.control_socket.settimeout(5.0)
            self.control_socket.connect((self.address, self.control_port))
            self.control_connected = True
            
            self.log_message.emit("debug", f"Connected to control server at {self.address}:{self.control_port}")
            return True
            
        except Exception as e:
            self.log_message.emit("error", f"Failed to connect to control server: {e}")
            return False
    
    def connect_data(self):
        """Connect to data streaming server"""
        try:
            if self.data_socket:
                self.data_socket.close()
            
            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.settimeout(5.0)
            self.data_socket.connect((self.address, self.data_port))
            self.data_connected = True
            
            self.log_message.emit("debug", f"Connected to data server at {self.address}:{self.data_port}")
            
            # Start data receiving thread
            data_thread = DataStreamer(running=self.streaming_active)
            data_thread.data_received.connect(self.data_received)
            data_thread.log_message.connect(self.log_message)
            data_thread.start()
            
            return True
            
        except Exception as e:
            self.log_message.emit("error", f"Failed to connect to data server: {e}")
            return False
    
    def disconnect_from_server(self):
        """Disconnect from servers"""
        if self.control_socket:
            try:
                self.control_socket.close()
            except:
                pass
            self.control_socket = None
        
        if self.data_socket:
            try:
                self.data_socket.close()
            except:
                pass
            self.data_socket = None
        
        if self.control_connected or self.data_connected:
            self.control_connected = False
            self.data_connected = False
            self.server_disconnected.emit()
            self.log_message.emit("info", "Disconnected from server")
    
    def send_control_command(self, command_type, params=None):
        """Send control command to server"""
        if not self.control_connected:
            self.error_occurred.emit("Not connected to control server")
            return None
        
        if command_type not in SUPPORTED_COMMANDS: 
            self.error_occurred.emit("Invalid command sent")
            return None
        
        command = {
            'type': command_type.value,
            'params': params or {}
        }
        
        try:
            command_data = json.dumps(command).encode('utf-8')
            self.control_socket.send(command_data)
            
            response_data = self.control_socket.recv(MAX_BUFFER_SIZE)
            response = json.loads(response_data.decode('utf-8'))
            
            return response
            
        except Exception as e:
            self.error_occurred.emit(f"Control communication error: {e}")
            self.disconnect_from_server()
            return None
    
    def ping_server(self):
        """Test server connectivity"""
        response = self.send_control_command(Command.PING)
        
        if response and response.get('type') == 'success':
            server_info = response.get('server_info', {})
            self.log_message.emit("info", f"Server ping successful - {server_info.get('server_type', 'unknown')}")
            return True
        else:
            self.log_message.emit("error", "Server ping failed")
            return False
    
    def discover_devices(self):
        """Discover devices"""
        self.log_message.emit("info", "Discovering devices...")
        response = self.send_control_command(Command.DISCOVER)
        
        if response and response.get('type') == 'success':
            devices = response.get('devices', [])
            self.log_message.emit("info", f"Found {len(devices)} devices")
            return devices
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Device discovery failed: {error_msg}")
            return []
    
    def connect_device(self, device_id = None):
        """Connect to device"""
        self.log_message.emit("info", f"Connecting...")
        response = self.send_control_command(Command.CONNECT, {'id': device_id})
        
        if response and response.get('type') == Response.SUCCESS.value:
            self.log_message.emit("info", "Device connected successfully")
            self.device_connected.emit(device_id)
            return True
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Device connection failed: {error_msg}")
            self.device_connection_failed.emit(device_id)
            return False
    
    def disconnect_device(self):
        """Disconnect from device"""
        self.log_message.emit("info", "Disconnecting device...")
        
        # Stop streaming first
        if self.streaming_active:
            self.stop_streaming()
        
        response = self.send_control_command(Command.DISCONNECT)
        
        if response and response.get('type') == 'success':
            self.log_message.emit("info", "Device disconnected")
            self.device_disconnected.emit()
            return True
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Disconnect failed: {error_msg}")
            return False
    
    def start_streaming(self):
        """Start real-time data streaming"""
        self.log_message.emit("info", "Starting data streaming...")
        response = self.send_control_command(Command.START)
        
        if response and response.get('type') == 'success':
            self.streaming_active = True
            self.log_message.emit("info", "Data streaming started")
            self.streaming_started.emit()
            
            # Connect to data server
            if not self.data_connected:
                self.connect_data()
            
            return True
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Failed to start streaming: {error_msg}")
            return False
    
    def stop_streaming(self):
        """Stop data streaming"""
        self.log_message.emit("info", "Stopping data streaming...")
        
        self.streaming_active = False
        
        # Disconnect data socket
        if self.data_socket:
            try:
                self.data_socket.close()
            except:
                pass
            self.data_socket = None
            self.data_connected = False
        
        response = self.send_control_command(Command.STOP)
        
        if response and response.get('type') == 'success':
            self.log_message.emit("info", "Data streaming stopped")
            self.streaming_stopped.emit()
            return True
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Failed to stop streaming: {error_msg}")
            return False
    
    def configure_device(self, device_id, config):
        """Configure device parameters"""
        self.log_message.emit("info", "Configuring device: {device_id}")
        response = self.send_control_command(Command.CONFIGURE, {'id': device_id, 'config': config})
        
        if response and response.get('type') == 'success':
            self.log_message.emit("info", "Device configured successfully")
            return True
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Configuration failed: {error_msg}")
            return False
        
    def update_params(self, config): 
        pass 
    
class DataStreamer(QThread): 
    log_message = pyqtSignal(str, str)
    data_received = pyqtSignal(np.ndarray)
    
    def __init__(self, running, parent = None): 
        super().__init__(parent)
        self.running = running
    
    def run(self):
        """Receive real-time data from server"""
        self.log_message.emit("info", "Data receiving thread started")
        
        while self.running:
            try:
                # Receive data length header
                length_data = self._recv_exactly(4)
                if not length_data:
                    break
                
                data_length = struct.unpack('!I', length_data)[0]
                
                # Receive the actual data
                data_bytes = self._recv_exactly(data_length)
                if not data_bytes:
                    break
                
                # Deserialize the data
                data = self._deserialize_data(data_bytes)
                
                if data is not None:
                    # Emit data signal for plotting
                    self.data_received.emit(data)
                
            except Exception as e:
                if self.streaming_active:
                    self.log_message.emit("error", f"Data receiving error: {e}")
                break
        
        self.log_message.emit("info", "Data receiving thread stopped")
        
    def _recv_exactly(self, num_bytes):
        """Receive exactly num_bytes from data socket"""
        data = b''
        while len(data) < num_bytes:
            try:
                chunk = self.data_socket.recv(num_bytes - len(data))
                if not chunk:
                    return None
                data += chunk
            except:
                return None
        return data
    
    def _deserialize_data(self, data_bytes):
        """Deserialize numpy data from server"""
        try:
            # Read header length
            header_length = struct.unpack('!I', data_bytes[:4])[0]
            
            # Read header
            header_bytes = data_bytes[4:4+header_length]
            header = json.loads(header_bytes.decode('utf-8'))
            
            # Read data
            array_bytes = data_bytes[4+header_length:]
            
            # Reconstruct numpy array
            shape = tuple(header['shape'])
            dtype = np.dtype(header['dtype'])
            
            data = np.frombuffer(array_bytes, dtype=dtype).reshape(shape)
            
            return data
            
        except Exception as e:
            self.log_message.emit("error", f"Data deserialization error: {e}")
            return None
        
    def stop(self): 
        self.running = False
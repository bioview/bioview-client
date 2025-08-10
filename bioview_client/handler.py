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
from typing import List, Tuple, Dict, Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from bioview_common import (
    ClientStatus, get_ip, get_app_info, AuthenticationError, ValidationError, SUPPORTED_RESPONSES,
    DataSource, Command, SUPPORTED_COMMANDS, Response, MAX_BUFFER_SIZE, AUTH_TIMEOUT
)

class Client(QThread):
    # Server control signals 
    server_scan_completed = pyqtSignal()
    server_connected = pyqtSignal(bool)
    server_disconnected = pyqtSignal(bool)
    
    # Server info signals 
    server_scan_progress = pyqtSignal(int)
    server_status = pyqtSignal(dict)
    
    # Device control signals 
    device_connected = pyqtSignal(str, bool)
    device_disconnected = pyqtSignal(str, bool)
    streaming_started = pyqtSignal(bool)
    streaming_stopped = pyqtSignal(bool)
    
    # Device info signals 
    devices_discovered = pyqtSignal(dict)
    
    # General info signals
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str, str)
    
    # Data signals for graphical output
    data_received = pyqtSignal(DataSource, np.ndarray) 
    
    def __init__(
        self, 
        data_port: int = 9998, 
        control_port: int = 9999,
        auth_timeout: int = AUTH_TIMEOUT,
        resp_timeout: int = RESPONSE_TIMEOUT
    ):
        super().__init__()
        self.app_info = get_app_info() # used for server handshake
        self.auth_timeout = auth_timeout
        self.resp_timeout = resp_timeout

        # Client network parameters
        self.address: str = get_ip() 
        self.network_prefix: str = self.address[:self.address.rindex('.')]

        # Servers
        self.discovered_servers: List[Dict] = []
        self.selected_server: Dict = {} # (hostname: IP)
        
        # Ports 
        self.data_port: int = data_port
        self.control_port: int = control_port
        
        # Threads
        self.data_thread = None
        self.control_thread = None 
        
        # Sockets
        self.data_socket = None
        self.control_socket = None
        
        # Client state
        self.status = ClientStatus.DEFAULT
        
    ### Server commands 
    def ping_server(self):
        """Test server connectivity"""
        response, response_code = self.send_control_command(Command.PING_SERVER)
        
        if response_code == 'success':
            server_info = response.get('server_info', {})
            self.log_message.emit("info", f"Server ping successful - {server_info.get('server_type', 'unknown')}")
            return True
        else:
            self.log_message.emit("error", "Server ping failed")
            return False

    def discover_servers(self): 
        # Scan IP range
        for i in range(1, 255):
            if self.status != ClientStatus.SCANNING:
                break
                
            target_ip = f"{self.network_prefix}.{i}"
            
            # TODO: Add message logging to text box 
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
            self.server_scan_progress.emit(progress)
        
        self.server_scan_completed.emit(True)
    
    # Authentication check for initial connection to server
    def authenticate_with_server(
        self, 
        server_address: Tuple[str, int], # (address, control port)
        server_socket: socket.socket,  
    ) -> Dict[str, Any]:
        """
        Perform handshake with server. Returns server info if successful, raises AuthenticationError if failed
        """
        server_ip = server_address[0]
        
        try:
            server_socket.settimeout(self.auth_timeout)
            
            # Step 1: Broadcast client info to server (syn)
            client_syn = {
                'type': Command.CONNECT_SERVER.value,
                'payload': {
                    "hostname": self.app_info['hostname'], 
                    "app_name": self.app_info['app_name'], # TODO: Replace with app_token
                    "app_version": self.app_info['version'],
                    "timestamp": time.time()
                }
            }    
            
            client_syn_json = json.dumps(client_syn).encode('utf-8')
            server_socket.send(client_syn_json)
            
            # Step 2: Receive server challenge or connection refusal (ack)
            response_data = server_socket.recv(4096).decode('utf-8')
            if not response_data:
                raise AuthenticationError("No response received from server")
            
            try:
                server_response = json.loads(response_data)
                server_response_type = server_response.get('type')
                server_response_payload = server_response.get('payload')
            except json.JSONDecodeError:
                raise AuthenticationError("Invalid JSON in server response")
            
            # Extract server hostname info
            server_hostname = server_response_payload.get('server_hostname', server_ip)

            # Check if connection was refused
            if server_response_type == Response.CONNECTION_REFUSED.value:
                message = server_response_payload.get('message', 'Connection refused by server')
                raise AuthenticationError(f"Connection refused by {server_hostname}: {message}")
            
            # Authenticate server using challenge 
            challenge = server_response_payload.get('challenge', None)
            if not challenge:
                raise AuthenticationError("Server did not provide authentication token")
            auth_token = self._get_challenge_response(challenge)
            client_response = {
                'type': Command.AUTHENTICATE_CLIENT.value,
                'payload': {
                    'token': auth_token,
                    'timestamp': time.time()
                }
            }
            response_data = json.dumps(client_response).encode('utf-8')
            server_socket.send(response_data)
            
            auth_result_data = server_socket.recv(4096).decode('utf-8')
            if not auth_result_data:
                raise AuthenticationError("Unable to authenticate server: No authentication result received")
            
            try:
                auth_result = json.loads(auth_result_data)
            except json.JSONDecodeError:
                raise AuthenticationError("Unable to authenticate server: Authentication result malformed.")
            
            if auth_result.get('type') != Response.AUTHENTICATION_SUCCESS.value:
                raise AuthenticationError("Server authentication failed")
            
            server_info = auth_result.get('payload').get('server_info', {})
            
            # Ensure hostname fallback for display
            if 'hostname' not in server_info or not server_info['hostname']:
                server_info['hostname'] = server_ip
            
            self.logger.info(f"Successfully authenticated with server: {server_info.get('hostname')} ({server_ip})")
            return server_info
            
        except socket.timeout:
            raise AuthenticationError("Authentication timeout")
        except Exception as e:
            self.logger.error(f"Client authentication error: {e}")
            raise AuthenticationError(f"Authentication failed: {str(e)}")
    
    # Validation for received responses to ensure integrity
    def _validate_message_format(self, data: Dict[str, Any], expected_fields: list) -> bool:
        """Validate that message contains required fields and proper format"""
        if not isinstance(data, dict):
            return False
        
        for field in expected_fields:
            if field not in data:
                return False
        
        return True
    
    def validate_response(self, data: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        timeout = timeout or self.response_timeout
        
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            raise ValidationError("Invalid JSON format")
        
        # Validate message structure
        required_fields = ['type', 'payload']
        if not self._validate_message_format(message, required_fields):
            raise ValidationError("Message missing required fields")
        
        # Validate response type
        response_type = message.get('type')
        if response_type not in SUPPORTED_RESPONSES:
            raise ValidationError(f"Unsupported response: {response_type}")
        
        # Validate payload is a dictionary
        if not isinstance(message.get('payload'), dict):
            raise ValidationError(f"payload must be a dict but got {type(message.get('payload'))} instead")
        
        return message
    
    def connect_to_server(self):
        if self.selected_server == {}: 
            self.log_message.emit('warn', 'No server selected for connection. Automatically choosing an available server.')
            self.discover_servers()
            if len(self.discovered_servers) == 0: 
                self.log_message.emit('error', 'No valid servers available.')
            else: 
                self.selected_server = self.discovered_servers[0]
                self.log_message.emit('info', f'Connecting to server: {self.selected_server}')

        try:
            # Connect to control server - close pre-existing connections
            if self.control_socket:
                self.control_socket.close()
            
            self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.control_socket.settimeout(5.0)
            self.control_socket.connect((self.selected_server['address'], self.control_port))
            self.control_connected = True
            
            self.log_message.emit("debug", "Connected to control server")
            
            # Connect to data server - close pre-existing connections
            if self.data_socket:
                self.data_socket.close()
            
            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.settimeout(5.0)
            self.data_socket.connect((self.selected_server['address'], self.data_port))
            self.data_connected = True
            
            self.log_message.emit("debug", f"Connected to data server")

            # Emit status 
            self.status = ClientStatus.SERVER_CONNECTED
            self.server_connected.emit(True)

        except Exception as e:
            self.status = ClientStatus.DEFAULT
            self.log_message.emit("error", f"Server connection failed: {e}")
            self.server_connected.emit(False)

    def disconnect_from_server(self):
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
        
        self.status = ClientStatus.SERVER_DISCONNECTED
        self.server_disconnected.emit(True)         
        self.log_message.emit("info", "Disconnected from server")
    
    ### Device Commands 
    

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
        # Once server is connected, start sending commands and listening for data. 
        self.log_message.emit("info", "Starting client handler...")
        
        while self.running:

            if self.status != ClientStatus.SERVER_CONNECTED:
                
            # Try to maintain control connection
                if self.connect_control():
                    self.server_connected.emit()
                else:
                    time.sleep(2)
                    continue
            
            # If streaming is active, maintain data connection
            if self.streaming_active and not self.data_connected:
                self.connect_data()
            
            time.sleep(0.1)
    
    ### General functions
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
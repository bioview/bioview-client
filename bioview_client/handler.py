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
from typing import List, Dict
from enum import Enum, auto

from PyQt6.QtCore import QThread, QObject, pyqtSignal

from bioview_common import get_ip, APP_VERSION, DataSource, Command, SUPPORTED_COMMANDS, Response, MAX_BUFFER_SIZE 

class CLIENT_STATUS(Enum):
    DEFAULT = auto()      # Nothing is going on by default
    SCANNING = auto()
    SERVER_CONNECTED = auto()
    SERVER_DISCONNECTED = auto()
    STREAMING = auto()

class Client(QObject):
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
    
    def __init__(self, data_port=9998, control_port=9999):
        super().__init__()
        
        # Client parameters
        self.address: str = get_ip() 
        self.network_prefix: str = self.address[:self.address.rindex('.')]
        self.running: bool = False
        self.status: CLIENT_STATUS = CLIENT_STATUS.DEFAULT

        # Servers
        self.discovered_servers: Dict[str, str] = {} # (server_id, IP)
        self.connected_server: str =  "" # server_id
        self.server_number: int = 1 # Just a temporary initialization constant
        
        # Devices
        self.discovered_devices: Dict[str, str] = {} # (device_id, server_id)
        self.connected_devices: Dict[str, str] = {} # (device_id, server_id)
        self.streaming_active: bool = False

        # Ports 
        self.data_port: int = data_port
        self.control_port: int = control_port
        
        # Threads
        # self.data_thread = None
        # self.control_thread = None 
        
        # Sockets
        self.data_socket: socket.socket = None
        self.control_socket: socket.socket = None
        
    
    def parse_event(self, event: Dict = None) -> None:
        if event.get('type') is None:
            self.error_occurred.emit("Error occurred becuase no command was specified")
        elif event.get('type') not in SUPPORTED_COMMANDS:
            self.error_occurred.emit("Error occurred because the command specified is currently not supported by the application")
        else:
            event_type = event.get('type')
            event_data = event.get('payload')
            match event_type:
                case 'PING_SERVER':
                    if len(self.discovered_servers) == 0:
                        self.error_occurred.emit("No server has been discovered yet")
                        return None
                    elif event_data is None:
                        self.log_message.emit('warn', 'No server is specified for pinging. Automatically choosing an available server')
                        server_id = list(self.discovered_servers.keys())[0]
                    else:
                        server_id = event_data.get('server_id')
                        if server_id is None:
                            self.log_message.emit('warn', 'No server is specified for pinging. Automatically choosing an available server')
                            server_id = list(self.discovered_servers.keys())[0]
                        elif server_id not in self.discovered_servers:
                            self.error_occurred.emit(f"The server {server_id} has not been discovered yet")
                            return None
                    self.ping_server(server_id)

                case 'DISCOVER_SERVERS':
                    if event_data is not None or event_data is not {}:
                        self.log_message.emit('No need to send any payload data for discovering servers')
                    self.discover_servers()

                case 'CONNECT_SERVER':
                    if len(self.discovered_servers) == 0:
                        self.error_occurred.emit("No server has been discovered yet")
                        return None
                    elif event_data is None:
                        self.log_message.emit('warn', 'No server is specified for connecting. Automatically choosing an available server')
                        server_id = list(self.discovered_servers.keys())[0]
                    else:
                        server_id = event_data.get('server_id')
                        if server_id is None:
                            self.log_message.emit('warn', 'No server is specified for connecting. Automatically choosing an available server')
                            server_id = list(self.discovered_servers.keys())[0]
                        elif server_id not in self.discovered_servers:
                            self.error_occurred.emit(f"The server {server_id} has not been discovered yet")
                            return None
                    self.connect_to_server(server_id)
                
                case 'DISCONNECT_SERVER':
                    if event_data is None or event_data.get('server_id') is None:
                        server_id = self.connected_server
                    else:
                        server_id = event_data.get('server_id')
                        if server_id != self.connected_server:
                            self.error_occurred.emit(f"The server {server_id} hasn't been connected")
                            return None
                    self.disconnect_from_server(server_id)
                
                case 'DISCOVER_DEVICES':
                    if event_data is None or event_data.get('server_id') is None:
                        self.log_message.emit('warn', 'No server is specified for discovering devices. Automatically choosing an available server')
                        server_id = list(self.discovered_servers.keys())[0]
                    else:
                        server_id = event_data.get('server_id')
                        if server_id not in self.discovered_servers:
                            self.error_occurred.emit(f"The server {server_id} has not been discovered yet")
                            return None
                    self.discover_devices(server_id)
                
                case 'INITIALIZE_DEVICES':
                    if event_data is None or event_data.get('device_ids') is None:
                        self.error_occurred.emit('At least one device id must be mentioned for initialization')
                        return None
                    else:
                        device_ids = event_data.get('device_ids')
                        false_devices = []
                        for device_id in device_ids:
                            if device_id not 
                        req_servers = {self.discovered_devices.get(idx) for idx in device_ids}
                        if 
                    self.update_device_configs(event.get('payload'))
                
                case 'CONNECT_DEVICES':
                    if event_data is None or event_data.get('device_ids') is None:
                        self.error_occurred.emit('At least one device id must be mentioned for connecting')
                        return None
                    else:
                        device_ids = event_data.get('device_ids')
                        false_devices = []
                        for device_id in device_ids:
                            if device_id not in self.discovered_devices:
                                false_devices.append(device_id)
                                del(device_ids[device_id])
                        if len(false_devices) == 1:
                            self.error_occurred.emit(f"The device {false_devices[0]} can't be found")
                        elif len(false_devices) > 1:
                            self.error_occurred.emit("The devices " + ",".join(false_devices) + " can't be found")
                        if len(device_ids) == 0:
                            self.error_occurred.emit('At least one correct device id must be mentioned for connecting')
                            return None 
                    self.connect_devices(device_ids)
                
                case 'DISCONNECT_DEVICES':
                    if event_data is None or event_data.get('device_ids') is None:
                        self.error_occurred.emit('At least one device id must be mentioned for disconnecting')
                        return None
                    else:
                        device_ids = event_data.get('device_ids')
                        false_devices = []
                        for device_id in device_ids:
                            if device_id not in self.discovered_devices:
                                false_devices.append(device_id)
                                del(device_ids[device_id])
                        if len(false_devices) == 1:
                            self.error_occurred.emit(f"The device {false_devices[0]} can't be found")
                        elif len(false_devices) > 1:
                            self.error_occurred.emit("The devices " + ",".join(false_devices) + " can't be found")
                        if len(device_ids) == 0:
                            self.error_occurred.emit('At least one correct device id must be mentioned for disconnecting')
                            return None 
                    self.disconnect_devices(device_ids)
                
                case 'START_STREAMING':
                    self.start_streaming(event.get('payload'))
                
                case 'STOP_STREAMING':
                    self.stop_streaming(event.get('payload'))
                
                case 'GET_DEVICE_STATUS':
                    pass
                
                case 'UPDATE_DEVICE_FIRMWARE':
                    pass
                
                case 'UPDATE_RUNNING_PARAMETER':
                    pass
                
                case _:
                    pass
        return None

    ### Server commands
    #Handled
    def ping_server(self, server_id: str) -> None:
        """Test server connectivity"""
        response = self.send_control_command(Command.PING_SERVER)
        
        if response.get('type') == 'success':
            server_info = response.get('payload').get('server_info', {})
            self.log_message.emit("info", f"Server ping successful - {server_info.get('server_type', 'unknown')}")
            return True
        else:
            self.log_message.emit("error", "Server ping failed")
            return False

    def discover_servers(self) -> None: 
        # Scan IP range
        for i in range(1, 255):
            if self.status != CLIENT_STATUS.SCANNING:
                break
                
            target_ip = f"{self.network_prefix}.{i}"
            
            try:
                # Quick connection test
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)  # 1sec timeout
                
                # NOTE: This may be potentially vulnerable. However, since this is being done over a local network itself, the risks may be low. 
                # TODO: Confirm security with someone who deals with a networking stack. 

                # We will only try connecting to control ports
                result = sock.connect_ex((target_ip, self.control_port))
                
                if result == 0:
                    # Server found
                    server_id = self.generate_server_id()
                    self.server_found.emit(server_id, target_ip, self.data_port, self.control_port)
                
                sock.close()
                
            except Exception:
                pass
            
            # Update progress
            progress = int((i / 254) * 100)
            self.server_scan_progress.emit(progress)
        
        self.server_scan_completed.emit(True)

    def connect_to_server(self, server_id: str) -> None:
        try:
            # Connect to control server - close pre-existing connections
            if self.connected_server != server_id:
                self.disconnect_from_server(self.connected_server)
            
                self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.control_socket.connect((server_id, self.control_port))
                self.control_socket.settimeout(5.0)
                self.control_connected = True
                self.log_message.emit("debug", f"Connected to {server_id} control port")
                
                self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.data_socket.connect((server_id, self.data_port))
                self.data_socket.settimeout(5.0)
                self.data_connected = True
                self.log_message.emit("debug", f"Connected to {server_id} data port")

                # Emit status 
                self.status = CLIENT_STATUS.SERVER_CONNECTED
                self.server_connected.emit(True)

        except Exception as e:
            self.status = CLIENT_STATUS.DEFAULT
            self.log_message.emit("error", f"Server connection failed with error: {e}")
            self.server_connected.emit(False)

    def disconnect_from_server(self, server_id: str) -> None:
        try:
            self.control_socket.close()
        except:
            pass
        self.control_socket = None
        self.control_connected = False
        
        try:
            self.data_socket.close()
        except:
            pass
        self.data_socket = None
        self.data_connected = False
        
        self.status = CLIENT_STATUS.SERVER_DISCONNECTED
        self.server_disconnected.emit(True)         
        self.log_message.emit("info", "Disconnected from server")
        self.connected_server = ""
    
    ########## Device Commands ########## 
    
    '''
    def start_client(self):
        """Start the client worker"""
        self.running = True
        self.start()
    '''

    def stop_client(self):
        """Stop the client worker"""
        self.running = False
        self.disconnect_from_server()
        # self.quit()
        # self.wait()
    '''
    def run(self):
        # Once server is connected, start sending commands and listening for data. 
        self.log_message.emit("info", "Starting client handler...")
        self.running = True
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
    '''
    ### General functions

    def send_control_command(self, command_type: str, params: Dict = None):
        """Send control command to server"""
        if not self.control_connected:
            self.error_occurred.emit("Not connected to control server")
            self.disconnect_from_server()
            return None
        
        if command_type not in SUPPORTED_COMMANDS: 
            self.error_occurred.emit("Invalid command sent")
            self.disconnect_from_server()
            return None
        
        command = {
            'type': command_type.value,
            'params': {} if params is None else params
        }
        
        try:
            command_data = json.dumps(command).encode('utf-8')
            self.control_socket.send(command_data)
            
            response_data = self.control_socket.recv(MAX_BUFFER_SIZE)
            response = json.loads(response_data.decode('utf-8'))
            
            return response
            
        except Exception as e:
            self.error_occurred.emit(f"Control communication error: {e}")
            #self.disconnect_from_server()
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
    
    def connect_devices(self, device_ids: List = None):
        """Connect to devices"""
        self.log_message.emit("info", f"Connecting...")
        devices_status = []
        for device_id in device_ids:
            response = self.send_control_command(Command.CONNECT, {'id': device_id})
        
            if response and response.get('type') == 'success':
                self.log_message.emit("info", f"Device {device_id} connected successfully")
                self.device_connected.emit(device_id)
                devices_status.append(True)
                self.connect_devices.append(device_id)
            else:
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                self.error_occurred.emit(f"Device connection failed: {error_msg}")
                self.device_connection_failed.emit(device_id)
                devices_status.append(False)
        
        return devices_status
    
    def disconnect_devices(self, device_ids: List = None):
        """Disconnect from device"""
        self.log_message.emit("info", "Disconnecting device...")
        devices_status = []
        if self.streaming_active:
            self.stop_streaming(device_ids)
        for device_id in device_ids:
            response = self.send_control_command(Command.DISCONNECT, {'id': device_id})
        
            if response and response.get('type') == 'success':
                self.log_message.emit("info", "Device disconnected")
                self.device_disconnected.emit(device_id)
                devices_status.append(True)
            else:
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                self.error_occurred.emit(f"Disconnect failed: {error_msg}")
                devices_status.append(False)
        
        return devices_status
    
    def start_streaming(self, device_ids: List = None):
        """Start real-time data streaming"""
        self.log_message.emit("info", "Starting data streaming...")
        devices_status = []
        for device_id in device_ids:
            response = self.send_control_command(Command.START, {'id': device_id})
        
            if response and response.get('type') == 'success':
                self.streaming_active = True
                self.log_message.emit("info", "Data streaming started")
                self.streaming_started.emit()
            
                # Connect to data server
                ############################# Need to clarify this ############################################
                # if not self.data_connected:
                #    self.connect_data()
            devices_status.append(True)
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            self.error_occurred.emit(f"Failed to start streaming: {error_msg}")
            devices_status.append(False)

        return devices_status


    def stop_streaming(self, device_ids: List = None):
        """Stop data streaming"""
        self.log_message.emit("info", "Stopping data streaming...")
        
        self.streaming_active = False
        devices_status = []
        # Disconnect data socket
        # if self.data_socket:
        #     try:
        #         self.data_socket.close()
        #     except:
        #         pass
        #     self.data_socket = None
        #     self.data_connected = False
        for device_id in device_ids:
            response = self.send_control_command(Command.STOP)
            
            if response and response.get('type') == 'success':
                self.log_message.emit("info", "Data streaming stopped")
                self.streaming_stopped.emit()
                devices_status.append(True)
            else:
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                self.error_occurred.emit(f"Failed to stop streaming: {error_msg}")
                devices_status.append(False)
        
        return devices_status
    

    def update_device_configs(self, device_ids: List = None, device_configs: List = None):
        """Configure device parameters"""
        assert len(device_ids) == len(device_configs), "Number of devices and configs doesn't match"
        
        devices_status = []
                
        for idx in range(len(device_ids)):
            self.log_message.emit("info", f"Configuring device: {device_ids[idx]}")
            response = self.send_control_command(Command.CONFIGURE, {'id': device_ids[idx], 'config': device_configs[idx]})
            
            if response and response.get('type') == 'success':
                self.log_message.emit("info", f"Device {device_ids[idx]} configured successfully")
                devices_status.append(True)
            else:
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                self.error_occurred.emit(f"Configuration failed: {error_msg}")
                devices_status.append(False)
        
        return devices_status
            
    
    def update_device_configs(self, device_ids: List = None, device_firmwares: List = None):
        """Configure device parameters"""
        assert len(device_ids) == len(device_firmwares), "Number of devices and firmware updates doesn't match"
        
        devices_status = []
                
        for idx in range(len(device_ids)):
            self.log_message.emit("info", f"Configuring device: {device_ids[idx]}")
            response = self.send_control_command(Command.CONFIGURE, {'id': device_ids[idx], 'config': device_firmwares[idx]})
            
            if response and response.get('type') == 'success':
                self.log_message.emit("info", f"Device {device_ids[idx]} configured successfully")
                devices_status.append(True)
            else:
                error_msg = response.get('message', 'Unknown error') if response else 'No response'
                self.error_occurred.emit(f"Configuration failed: {error_msg}")
                devices_status.append(False)

        return devices_status 

    def generate_server_id(self):
        self.server_number += 1
        return f'Server{self.server_number - 1}'


'''
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
'''

if __name__ == "__main__":
    client_handler = Client()
        
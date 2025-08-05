'''
Declares commonly supported commands that may be supported wholly or in part by different servers and clients 
'''
from enum import Enum 

MAX_BUFFER_SIZE = 4096

# Command from client to server 
class Command(Enum): 
    # Server specific controls 
    PING_SERVER = 'ping_server' # DONE 
    DISCOVER_SERVERS = 'discover_servers' # PARTIAL - Needs server info TLC
    CONNECT_SERVER = 'connect_server'
    DISCONNECT_SERVER = 'disconnect_server'
    GET_SERVER_STATUS = 'get_server_status'

    # Device specific controls (implemented in Device)
    DISCOVER_DEVICES = 'discover_devices'
    INIT_DEVICES = 'init_devices'
    CONNECT_DEVICES = 'connect_devices'
    DISCONNECT_DEVICES = 'disconnect_device'
    START_STREAMING = 'start_streaming'
    STOP_STREAMING = 'stop_streaming'
    GET_DEVICE_STATUS = 'get_device_status'
    UPDATE_DEVICE_CONFIGURATION = 'update_device_configuration'
    UPDATE_DEVICE_FIRMWARE = 'update_device_firmware'
 
SUPPORTED_COMMANDS = [x.name for x in Command]

# Response from server to client   
class Response(Enum): 
    SUCCESS = "success"
    ERROR = "error"
    INFO = "info"
    DEBUG = "debug"
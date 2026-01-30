""" Client-side handler

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
"""

import contextlib
import socket
import threading
import time
from typing import Any, Dict, List

import numpy as np
from bioview_common import (
    AUTH_TIMEOUT,
    CONTROL_PORT,
    DATA_PORT,
    RESPONSE_TIMEOUT,
    AuthenticationError,
    ClientStatus,
    Command,
    Configuration,
    DataSource,
    DeviceStatus,
    Response,
    get_app_info,
    get_ip,
)
from PyQt6.QtCore import QThread, QThreadPool, pyqtSignal

from bioview_client.helpers import DataStreamer, DeviceInitWorker, ScanWorker
from bioview_client.utils import (
    get_challenge_response,
    group_config_to_dict,
    is_dict_of_dicts,
    parse_and_validate_response,
    send_command,
)


class Client(QThread):
    # Server control signals
    server_scan_completed = pyqtSignal(list)
    server_connected = pyqtSignal(bool)
    server_disconnected = pyqtSignal(bool)

    # Server info signals
    server_scan_progress = pyqtSignal(int)
    server_status = pyqtSignal(dict)

    # Device control signals
    # Since all failure signals only need logging, we do
    # not add explicit signals for failure, only success
    devices_discovered = pyqtSignal(dict)
    device_init_succeeded = pyqtSignal(dict)
    device_status_updated = pyqtSignal(dict)
    device_disconnect_succeeded = pyqtSignal()

    # Streaming states
    streaming_started = pyqtSignal(bool)
    streaming_stopped = pyqtSignal(bool)

    # General info signals
    log_message = pyqtSignal(str, str)

    # Data signals for graphical outputi 
    data_received = pyqtSignal(DataSource, np.ndarray)

    def __init__(
        self,
        experiment_config: Dict = None,
        group_configs: Dict = None,
        data_port: int = DATA_PORT,
        control_port: int = CONTROL_PORT,
        auth_timeout: int = AUTH_TIMEOUT,
        resp_timeout: int = RESPONSE_TIMEOUT,
    ):
        super().__init__()
        self.info = get_app_info()
        self.auth_timeout = auth_timeout
        self.resp_timeout = resp_timeout

        self.address: str = get_ip()
        self.network_prefix: str = self.address[: self.address.rindex(".")]

        self.discovered_servers: List[Dict] = []
        self.selected_server: Dict = {}

        self.data_port: int = data_port
        self.control_port: int = control_port

        self.data_thread = None
        self.control_thread = None

        self.data_socket = None
        self.control_socket = None

        self.status = ClientStatus.DEFAULT
        self.data_connected = False

        self.data_sources = None 
        self.data_streamer = None

        # Thread pool for background tasks (scanning, device discovery)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(255)
        self._cancel_scan = False

        # Device discovery state
        self._discovering_devices = False

        # Lock to serialize control (and related socket) operations to avoid races
        # between send/recv and close operations from different threads.
        self._control_lock = threading.Lock()

        # Running state for the QThread
        self.running = False

        # Keep track of common configuration
        self.experiment_config = None

        if experiment_config:
            if isinstance(experiment_config, Configuration):
                self.experiment_config = experiment_config
            elif isinstance(experiment_config, dict):
                self.experiment_config = Configuration.from_dict(
                    experiment_config, "experiment"
                )

        # Keep track of device configuration and states
        self.group_configs = group_config_to_dict(group_configs) if group_configs else {}

        # Now, while we understand devices in the form of groups, we keep track of
        # states for individual devices for clearer presentation in the UI
        self.device_states = {}
        for group_id, group_dict in self.group_configs.items():
            self.device_states[group_id] = {}

            for device_id, device_cfg in group_dict.items():
                self.device_states[group_id][device_id] = {
                    "group": group_id,
                    "id": device_id,
                    "config": device_cfg,
                    "status": DeviceStatus.DISCONNECTED,
                }

    # Thread handling
    def run(self):
        self.log_message.emit("info", "Starting client handler...")

        while self.running:
            try:
                # A tiny, non-essential message to test the connection.
                if not isinstance(self.status, ClientStatus):
                    self.status = ClientStatus.DEFAULT
            except (OSError, ConnectionResetError, BrokenPipeError):
                # These exceptions mean the connection is closed.
                self.disconnect_from_server()
            finally:
                time.sleep(10)  # Check every few seconds

    # Discover servers in parallel
    def discover_servers(self):
        self.status = ClientStatus.SCANNING
        self.discovered_servers = []

        # Track transient state for early stopping
        self._cancel_scan = False
        self._completed_scans = 0
        total_ips = 255  # Currently we are only scanning IPv4
        self._last_update_time = time.time()

        def handle_result(found):
            if self._cancel_scan:
                return
            self._completed_scans += 1

            # Only emit progress every 100ms or at the end
            now = time.time()
            if now - self._last_update_time >= 0.1 or self._completed_scans == total_ips:
                progress = int((self._completed_scans / total_ips) * 100)
                self.server_scan_progress.emit(progress)
                self._last_update_time = now

            # Ensure we received a dict
            if found and isinstance(found, dict):
                # Avoid duplicates
                addr = found.get("ip")

                if addr and not any(
                    s.get("ip") == addr for s in self.discovered_servers
                ):
                    self.discovered_servers.append(found)

            if self._completed_scans == total_ips:
                self.status = ClientStatus.DEFAULT
                # discovery results updated
                self.server_scan_completed.emit(self.discovered_servers)

        for i in range(1, 255):
            if self._cancel_scan:
                break
            target_ip = f"{self.network_prefix}.{i}"
            worker = ScanWorker(target_ip, self.control_port)
            worker.signals.result.connect(handle_result)
            self.thread_pool.start(worker)

    def cancel_scan(self):
        self._cancel_scan = True
        self.thread_pool.clear()

        self.server_scan_completed.emit(self.discovered_servers)

    def _authenticate_with_server(self, server_socket: socket.socket) -> Dict[str, Any]:
        '''
        We try to authenticate ourselves with the server. In case the server closes
        the connection, we handle it gracefully (not that anything else can be done)
        '''
        server_socket.settimeout(self.auth_timeout)
        server_info = None 

        try: 
            # Broadcast client info to server and get response
            response = send_command(
                sock=server_socket,
                command=Command.CONNECT_SERVER,
                params={"client_info": self.info, "timestamp": time.time()},
            )
            
            # If we are here, the server did not close connection. 
            resp_type, resp_payload = parse_and_validate_response(response)

            # Check if server provided a challenge
            if resp_type == Response.SERVER_CHALLENGE.name:
                challenge = resp_payload.get("challenge", None)

            if not challenge:
                raise AuthenticationError("Server did not provide authentication token")

            auth_token = get_challenge_response(challenge)

            auth_response = send_command(
                sock=server_socket,
                command=Command.AUTHENTICATE_CLIENT,
                params={"token": auth_token, "timestamp": time.time()},
            )

            # Check results
            auth_resp_type, auth_resp_payload = parse_and_validate_response(
                auth_response
            )

            if auth_resp_type == Response.AUTHENTICATION_SUCCESS.name:
                server_info = auth_resp_payload.get("server_info", None)

                # Update status
                self.status = ClientStatus.SERVER_CONNECTED

                self.log_message.emit(
                    "info", f"Successfully connected to {server_info.get('hostname')}"
                )
            else:
                err = auth_resp_payload.get("message", "")
                raise AuthenticationError(f"Server authentication failed: {err}")
        except Exception as e: 
            self.log_message("error", "Authentication with server failed")
        finally: 
            return server_info

    def change_selected_server(self, index: int):
        if self.discovered_servers is None or len(self.discovered_servers) == 0:
            self.selected_server = {}
        elif index < 0 or index >= len(self.discovered_servers):
            self.selected_server = self.discovered_servers[0]
        else:
            self.selected_server = self.discovered_servers[index]

    def connect_to_server(self):
        if len(self.discovered_servers) == 0:
            self.discover_servers()

        if self.selected_server == {}:
            self.log_message.emit(
                "warn",
                "No server selected. Automatically connecting to an available server.",
            )
            if len(self.discovered_servers) == 0:
                self.log_message.emit("error", "No valid servers available.")
                return
            else:
                self.selected_server = self.discovered_servers[0]
                self.log_message.emit(
                    "info", f"Connecting to server: {self.selected_server}"
                )

        try:
            # Connect to control server - close pre-existing connections
            with self._control_lock:
                if self.control_socket:
                    with contextlib.suppress(Exception):
                        self.control_socket.close()

                self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.control_socket.settimeout(5.0)
                self.control_socket.connect(
                    (self.selected_server["ip"], self.control_port)
                )

            # Perform authentication handshake over the connected control socket
            server_info = self._authenticate_with_server(self.control_socket)

            # Update selected_server with returned info
            if server_info:
                # Connect data - close pre-existing connections
                if self.data_socket:
                    self.data_socket.close()

                self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.data_socket.settimeout(5)
                self.data_socket.connect((self.selected_server["ip"], self.data_port))

                # Update server info
                self.selected_server.update(server_info)

            # Once everything succeeds, update the status
            self.status = ClientStatus.SERVER_CONNECTED
            self.server_connected.emit(True)
    
            # Since the connection has completed successfully, we update data sources 
            # using server info 
            self.data_sources = server_info.get('data_sources', None)
        except Exception as e:
            # Reset status
            self.status = ClientStatus.DEFAULT

            # Reset sockets
            with contextlib.suppress(Exception):
                self.control_socket.close()
            self.control_socket = None
            with contextlib.suppress(Exception):
                self.data_socket.close()
            self.data_socket = None

            # Log message in UI
            self.log_message.emit("error", f"Server connection failed: {e}")

    def disconnect_from_server(self):
        # Locks for concurrency
        with self._control_lock:
            if self.control_socket:
                with contextlib.suppress(Exception):
                    self.control_socket.close()
                self.control_socket = None

            if self.data_socket:
                with contextlib.suppress(Exception):
                    self.data_socket.close()
                self.data_socket = None

        self.status = ClientStatus.SERVER_DISCONNECTED

        try:
            self.server_disconnected.emit(True)
        except Exception:
            self.server_disconnected.emit()

        self.log_message.emit("info", "Disconnected from server")

    ### Device Commands
    def initialize_devices(self, only_discover: bool = False):
        # Avoid starting another discovery while one is active
        if self._discovering_devices:
            self.log_message.emit("warn", "Device discovery already in progress")
            return

        self._discovering_devices = True

        if only_discover:
            cmd = Command.DISCOVER_DEVICES
            self.log_message.emit("debug", "Discovering devices...")
        else:
            cmd = Command.INITIALIZE_DEVICES
            self.log_message.emit("debug", "Initializing devices...")

        worker = DeviceInitWorker(client_ref=self, command=cmd)

        def _on_finished(group_status_dict):
            """
            Server response for discovered groups follows the convention -
            'group_id': {
                'device_id': {
                    'status': DeviceStatus
                }
            }

            Note that the response will only contain keys for device groups
            that were requested in the provided payload. If no groups were
            specified, nothing will be printed out.
            """
            if not group_status_dict or not is_dict_of_dicts(group_status_dict):
                self._discovering_devices = False

                if len(self.group_configs) > 0: 
                    self.log_message.emit("warning", f"Invalid response received")
                return

            self.device_states = group_status_dict

            # Update state and emit appropriate signal
            if only_discover:
                self.status = ClientStatus.DEVICES_DISCOVERED
                self.devices_discovered.emit(self.device_states)
            else:
                self.status = ClientStatus.DEVICES_CONNECTED
                self.device_init_succeeded.emit(self.device_states)

            self._discovering_devices = False

        worker.signals.finished.connect(_on_finished)
        self.thread_pool.start(worker)

    def disconnect_device(self):
        self.log_message.emit("info", "Disconnecting devices...")

        # Stop streaming first is currently going
        if self.status is ClientStatus.STREAMING:
            self.stop_streaming()

        response = send_command(
            sock=self.control_socket, command=Command.DISCONNECT_DEVICES
        )
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.log_message.emit("info", "Devices disconnected")
            self.device_disconnect_succeeded.emit()
        else:
            msg = resp_payload.get("message", "")
            self.log_message.emit("error", f"Disconnect failed: {msg}")

    # Data streaming handlers
    def start_streaming(self):
        if self._discovering_devices:
            self.log_message.emit(
                "warning", "Cannot start streaming while device discovery is in progress"
            )
            return False

        self.log_message.emit("info", "Attempting to start data streaming...")
        self.control_socket.settimeout(10)
        response = send_command(
            sock=self.control_socket,
            command=Command.START_STREAMING,
            params={
                "save_config": self.experiment_config.get_save_config(),
                "display_config": self.experiment_config.get_display_config(),
            },
        )
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            # Enter streaming state; data socket will be connected by run() or here
            self.status = ClientStatus.STREAMING
            self.log_message.emit("info", "Data streaming started")
            try:
                # Try to ensure data connection immediately
                if not self.data_connected:
                    if not self.data_socket:
                        self.data_socket = socket.socket(
                            socket.AF_INET, socket.SOCK_STREAM
                        )

                    self.data_socket.settimeout(10)
                    self.data_socket.connect(
                        (self.selected_server.get("ip"), self.data_port)
                    )
                    self.data_connected = True
            except Exception as e:
                self.log_message.emit("error", f"Data connection failed: {e}")

            # Start data streamer streamer
            self.data_streamer = DataStreamer(data_conn=self.data_socket)
            self.data_streamer.data_received.connect(self._handle_received_data)
            self.data_streamer.start()

            # Emit success
            self.streaming_started.emit(True)
            self.log_message.emit("debug", "Streaming started successfully")
        else:
            msg = resp_payload.get("message", "")
            self.log_message.emit("error", f"Failed to start streaming: {msg}")

    def _handle_received_data(self, source, data):
        # Forwards received (source, data) to UI for display
        # Any processing is expected to be done in DataStreamer
        self.data_received.emit(source, data)

    def stop_streaming(self):
        if self._discovering_devices:
            self.log_message.emit(
                "warning", "Cannot stop streaming while device discovery is in progress"
            )
            return False

        self.log_message.emit("debug", "Attempting to stop streaming...")

        self.control_socket.settimeout(10)
        response = send_command(sock=self.control_socket, command=Command.STOP_STREAMING)
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.ERROR.name:
            msg = f'Failed to stop streaming: {resp_payload.get('message', '')}'
            self.log_message.emit("error", msg)

        # Stop receiving data regardless
        if self.data_socket:
            with contextlib.suppress(Exception):
                self.data_socket.close()
            self.data_socket = None
            self.data_connected = False

        # Stop stream worker
        if self.data_streamer:
            self.data_streamer.stop()

        # Update status
        self.status = ClientStatus.DEVICES_CONNECTED
        self.streaming_stopped.emit(True)
        self.log_message.emit("debug", "Streaming stopped successfully")

    def configure_device(self, device_id, config):
        '''
        This function is used by BioView Configurator to modify operational parameters
        of connected devices using respective device handlers, which in turn will make
        calls using the provided device drivers. 
        '''
        if self._discovering_devices:
            self.log_message.emit(
                "warning",
                "Cannot configure device while device discovery is in progress",
            )
            return False

        self.log_message.emit("info", "Configuring device: {device_id}")
        response = send_command(
            sock=self.control_socket,
            command=Command.UPDATE_RUNNING_PARAMETER,
            params={"id": device_id, "config": config},
        )

        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.log_message.emit("debug", "Successfully updated device parameter")
            # TODO: Let UI know to update things correctly.
            return True
        else:
            msg = resp_payload.get("message", "")
            self.log_message.emit("debug", f"Failed to update parameter: {msg}")
            # TODO: Update UI
            return False

    # Client function for PyQt loops
    def start_client(self):
        self.running = True
        self.start()

    def stop_client(self):
        self.running = False
        self.disconnect_from_server()
        self.quit()

    ### Helpers
    def get_data_sources(self):
        return self.data_sources

    def update_device_state(self, device_id) -> bool:
        """Helper function to keep track of device states internally"""
        pass

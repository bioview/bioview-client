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
import json
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Tuple

import numpy as np
from bioview_common import (
    AUTH_TIMEOUT,
    CONTROL_PORT,
    DATA_PORT,
    MAX_BUFFER_SIZE,
    RESPONSE_TIMEOUT,
    SUPPORTED_COMMANDS,
    AuthenticationError,
    ClientStatus,
    Command,
    DataSource,
    DeviceStatus,
    Response,
    get_app_info,
    get_ip,
)
from PyQt6.QtCore import QObject, QRunnable, QThread, QThreadPool, pyqtSignal, pyqtSlot

from bioview_client.utils import is_dict_of_dicts, parse_and_validate_response


class ScanWorkerSignals(QObject):
    # Emit a server info dict when a BioView server is discovered, or None otherwise
    result = pyqtSignal(object)


class DeviceDiscoverSignals(QObject):
    # Emit a list of devices when discovery completes
    finished = pyqtSignal(dict)


class ScanWorker(QRunnable):
    def __init__(self, ip, control_port, timeout=0.5):
        super().__init__()
        self.ip = ip
        self.control_port = control_port
        self.timeout = timeout
        self.signals = ScanWorkerSignals()

    def run(self):
        # Probe the control port on the target IP and emit a server info dict or None
        with contextlib.suppress(Exception):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.ip, self.control_port))
            try:
                # Send a lightweight ping command and wait for a response
                ping = json.dumps(
                    {"type": Command.PING_SERVER.value, "payload": {}}
                ).encode("utf-8")
                with contextlib.suppress(Exception):
                    s.send(ping)
                    data = s.recv(4096)
                    if data:
                        try:
                            resp = parse_and_validate_response(data.decode("utf-8"))
                            payload = (
                                resp.get("payload", {}) if isinstance(resp, dict) else {}
                            )
                            server_info = payload.get("server_info", {})
                        except Exception:
                            server_info = {}
                        server_info["address"] = self.ip
                        self.signals.result.emit(server_info if server_info else None)
                        return
            finally:
                with contextlib.suppress(Exception):
                    s.close()

        # Default: emit None when no server found or on error
        with contextlib.suppress(Exception):
            self.signals.result.emit(None)


class DeviceDiscoverWorker(QRunnable):
    """Background worker to request device discovery without blocking the UI."""

    def __init__(self, client_ref):
        super().__init__()
        self.client_ref = client_ref
        self.signals = DeviceDiscoverSignals()

    @pyqtSlot()
    def run(self):
        try:
            devices = {}

            # Device discovery can be slow on server side; use a longer timeout and
            # do not disconnect the control socket on timeout so the client remains connected.
            response = self.client_ref.send_control_command(
                Command.DISCOVER_DEVICES,
                params={
                    "config": self.client_ref.common_config or {},
                    "groups": self.client_ref.group_configs or {},
                },
                timeout=30.0,
                disconnect_on_error=False,
            )

            if response and isinstance(response, dict):
                rtype = response.get("type")
                payload = response.get("payload", {})
                if rtype == Response.DEVICE_DISCOVERY_COMPLETED.value:
                    devices = payload.get("devices", {})

            # Validate for correctness of format
            if not is_dict_of_dicts(devices):
                self.client_ref.log_message.emit(
                    "debug",
                    f"Invalid device format received: {devices}. Expected dict-of-dicts.",
                )
                devices = {}

            self.signals.finished.emit(devices)
        except Exception:
            # On error, emit empty list
            self.signals.finished.emit({})


class Client(QThread):
    # Server control signals
    server_scan_completed = pyqtSignal(list)
    server_connected = pyqtSignal(bool)
    server_disconnected = pyqtSignal(bool)

    # Server info signals
    server_scan_progress = pyqtSignal(int)
    server_status = pyqtSignal(dict)

    # Device control signals
    device_connected = pyqtSignal(str, bool)
    device_disconnected = pyqtSignal(str, bool)
    # Emitted when a device connection attempt fails; provides device_id
    device_connection_failed = pyqtSignal(str)
    streaming_started = pyqtSignal(bool)
    streaming_stopped = pyqtSignal(bool)

    # Device info signals
    devices_discovered = pyqtSignal(dict)
    device_discovery_started = pyqtSignal()
    device_discovery_finished = pyqtSignal(list)

    # General info signals
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    # Data signals for graphical output
    data_received = pyqtSignal(DataSource, np.ndarray)

    def __init__(
        self,
        common_config: Dict = None,
        group_configs: Dict = None,
        data_port: int = DATA_PORT,
        control_port: int = CONTROL_PORT,
        auth_timeout: int = AUTH_TIMEOUT,
        resp_timeout: int = RESPONSE_TIMEOUT,
    ):
        super().__init__()
        self.app_info = get_app_info()
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
        self.control_connected = False
        self.authenticated = False

        self.status = ClientStatus.DEFAULT
        self.data_connected = False

        # Thread pool for background tasks (scanning, device discovery)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(20)
        self._cancel_scan = False

        # Device discovery state
        self._discovering_devices = False

        # If user requested an explicit/intentional disconnect, set this flag
        # so the background run-loop does not attempt to auto-reconnect.
        self._manual_disconnect = False

        # Lock to serialize control (and related socket) operations to avoid races
        # between send/recv and close operations from different threads.
        self._control_lock = threading.Lock()

        # Running state for the QThread
        self.running = False

        # Keep track of common configuration
        self.common_config = common_config or {}

        # Keep track of device configuration and states
        self.group_configs = group_configs or {}
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
                    "state": DeviceStatus.DISCONNECTED,
                }

    # Thread handling
    def run(self):
        self.log_message.emit("info", "Starting client handler...")
        while self.running:
            # Honor an explicit user-requested disconnect: do not auto-reconnect
            # while this flag is set. A subsequent user connect request must
            # clear this flag (see request_connect).
            if getattr(self, "_manual_disconnect", False):
                time.sleep(0.1)
                continue
            if self.status != ClientStatus.SERVER_CONNECTED:
                if self.connect_control():
                    # note: control socket connected but not necessarily authenticated
                    self.server_connected.emit(True)
                else:
                    time.sleep(2)
                    continue
            if self.status == ClientStatus.STREAMING and not self.data_connected:
                # Only connect data socket if authenticated with control channel
                if self.authenticated:
                    self.connect_data()
                else:
                    time.sleep(0.1)
                    continue
            time.sleep(0.1)

    ### Server commands
    def ping_server(self):
        """Test server connectivity"""
        response = self.send_control_command(Command.PING_SERVER)

        if response and response.get("type") == Response.INFO.value:
            payload = response.get("payload", {})
            server_info = (
                payload.get("server_info", {}) if isinstance(payload, dict) else {}
            )
            sanitized = self._sanitize_server_info(
                server_info, self.selected_server.get("address", "unknown")
            )
            # Ping success is noisy; log as debug to avoid cluttering info logs
            self.log_message.emit(
                "debug",
                f"Server ping successful - {sanitized.get('hostname')}",
            )
            return True
        else:
            self.log_message.emit("error", "Server ping failed")
            return False

    def connect_control(self) -> bool:
        """Attempt to establish control and data socket connections to the selected server.

        Returns True if control connection established, False otherwise.
        """
        try:
            # If we already have a control socket and it's flagged connected, return True
            if self.control_connected and self.control_socket:
                return True

            # Ensure selected_server exists
            if not self.selected_server:
                return False

            # Connect control socket (serialize with lock to avoid races)
            with self._control_lock:
                if self.control_socket:
                    self.control_socket.close()

                self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.control_socket.settimeout(5.0)
                self.control_socket.connect(
                    (self.selected_server["address"], self.control_port)
                )

            self.control_connected = True
            self.status = ClientStatus.SERVER_CONNECTED
            return True

        except Exception as e:
            self.log_message.emit("error", f"Control connection failed: {e}")
            self.control_connected = False
            self.control_socket = None
            return False

    def connect_data(self) -> bool:
        """Attempt to connect the data socket to the server's data port."""
        try:
            if self.data_connected and self.data_socket:
                return True

            if not self.selected_server:
                return False

            if self.data_socket:
                with contextlib.suppress(Exception):
                    self.data_socket.close()

            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.settimeout(5.0)
            self.data_socket.connect((self.selected_server["address"], self.data_port))
            self.data_connected = True
            return True
        except Exception as e:
            self.log_message.emit("error", f"Data connection failed: {e}")
            self.data_connected = False
            return False

    ### Server scan using QThreadPool
    def discover_servers(self):
        """Parallel server scanning using QThreadPool"""
        self.status = ClientStatus.SCANNING
        self._cancel_scan = False
        self.discovered_servers = []

        total_ips = 254
        self._completed_scans = 0
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

            # Collect servers if found (ScanWorker emits a dict or None)
            # Ensure we received a dict
            if found and isinstance(found, dict):
                # Avoid duplicates
                addr = found.get("address")
                if addr and not any(
                    s.get("address") == addr for s in self.discovered_servers
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
        """Cancel ongoing scan"""
        self._cancel_scan = True
        self.thread_pool.clear()

    # Authentication check for initial connection to server
    def authenticate_with_server(
        self,
        server_address: Tuple[str, int],  # (address, control port)
        server_socket: socket.socket,
    ) -> Dict[str, Any]:
        """
        Perform handshake with server.
        Returns server info if successful, raises AuthenticationError if failed.
        """
        server_ip = server_address[0]

        try:
            server_socket.settimeout(self.auth_timeout)

            # Step 1: Broadcast client info to server (syn)
            client_syn = {
                "type": Command.CONNECT_SERVER.value,
                "payload": {
                    "hostname": self.app_info["hostname"],
                    "app_name": self.app_info["app_name"],
                    "app_version": self.app_info["app_version"],
                    "timestamp": time.time(),
                },
            }

            server_socket.send(json.dumps(client_syn).encode("utf-8"))

            # Step 2: Receive server challenge or connection refusal (ack)
            response_type, response_payload = self._recv_and_validate_response(
                server_socket, expected_type=Response.SERVER_CHALLENGE.value
            )

            # Extract server hostname info
            server_hostname = response_payload.get("hostname", server_ip)

            # Check if connection was refused
            if response_type == Response.CONNECTION_REFUSED.value:
                message = response_payload.get("message", "Connection refused by server")
                raise AuthenticationError(
                    f"Connection refused by {server_hostname}: {message}"
                )

            # Authenticate server using challenge
            challenge = response_payload.get("challenge")
            if not challenge:
                raise AuthenticationError("Server did not provide authentication token")

            auth_token = self._get_challenge_response(challenge)
            client_response_dict = {
                "type": Command.AUTHENTICATE_CLIENT.value,
                "payload": {"token": auth_token, "timestamp": time.time()},
            }
            server_socket.send(json.dumps(client_response_dict).encode("utf-8"))

            auth_result_type, auth_result_payload = self._recv_and_validate_response(
                server_socket, expected_type=None
            )

            # normalize auth_result_type for expected success/failure
            auth_type_norm = (
                auth_result_type.lower()
                if isinstance(auth_result_type, str)
                else str(auth_result_type).lower()
            )
            if auth_type_norm not in {
                Response.AUTHENTICATION_SUCCESS.value.lower(),
                Response.AUTHENTICATION_FAILURE.value.lower(),
            }:
                raise AuthenticationError(
                    f"Unexpected auth response type: {auth_result_type}"
                )

            server_info = auth_result_payload.get("server_info", {})
            sanitized = self._sanitize_server_info(server_info, server_ip)

            self.authenticated = True
            self.log_message.emit(
                "info", f"Successfully connected to {sanitized.get('hostname')}"
            )
            return sanitized

        except socket.timeout:
            self.log_message.emit("error", "Authentication timeout")
            raise AuthenticationError("Authentication timeout") from None
        except AuthenticationError:
            raise
        except Exception as e:
            self.log_message.emit("error", f"Client authentication error: {e}")
            raise AuthenticationError(f"Authentication failed: {str(e)}") from None

    def _get_challenge_response(self, challenge: str) -> str:
        """Compute the response token for a given challenge.

        Uses SHA-256 over the canonical string "{challenge}:{app_version}" where
        app_version is taken from the client app info (or falls back to APP_VERSION).
        """
        try:
            # Prefer the packaged app version from app_info
            app_version = None
            try:
                app_version = self.app_info.get("app_version")
            except Exception:
                app_version = None

            if not app_version:
                # import here to avoid circular imports at module load
                from bioview_common import APP_VERSION

                app_version = str(APP_VERSION)

            import hashlib

            m = hashlib.sha256()
            m.update(f"{challenge}:{app_version}".encode())
            return m.hexdigest()
        except Exception:
            # As a fallback, return an empty token to force auth failure rather than crash
            return ""

    def _recv_and_validate_response(
        self, server_socket: socket.socket, expected_type=None
    ):
        """Receive raw data from server socket and validate it using network helper.

        Converts parsing/validation errors into AuthenticationError for callers.
        Returns (response_type, response_payload).
        """
        try:
            raw = server_socket.recv(4096).decode("utf-8")
        except Exception as e:
            raise AuthenticationError(f"Failed to receive response: {e}") from e

        try:
            return parse_and_validate_response(raw, response_type=expected_type)
        except Exception as e:
            # Map any parsing/validation error to AuthenticationError for caller clarity
            raise AuthenticationError(f"Invalid response from server: {e}") from e

    def _sanitize_server_info(self, server_info: dict, fallback_ip: str) -> dict:
        """Ensure server_info dict contains JSON-serializable primitives and hostname fallback."""
        sanitized = {}
        for k, v in (server_info or {}).items():
            try:
                json.dumps({k: v})
                sanitized[k] = v
            except Exception:
                sanitized[k] = str(v)

        if "hostname" not in sanitized or not sanitized.get("hostname"):
            sanitized["hostname"] = fallback_ip

        return sanitized

    def change_selected_server(self, index: int):
        if self.discovered_servers is None or len(self.discovered_servers) == 0:
            self.selected_server = {}
        elif index < 0 or index >= len(self.discovered_servers):
            self.selected_server = self.discovered_servers[0]
        else:
            self.selected_server = self.discovered_servers[index]

    def connect_to_server(self):
        if self.selected_server == {}:
            self.log_message.emit(
                "warn",
                "No server selected. Automatically connecting to an available server.",
            )
            self.discover_servers()
            if len(self.discovered_servers) == 0:
                self.log_message.emit("error", "No valid servers available.")
                self.server_connected.emit(False)
                return
            else:
                self.selected_server = self.discovered_servers[0]
                self.log_message.emit(
                    "info", f"Connecting to server: {self.selected_server}"
                )

        try:
            # Connect to control server - close pre-existing connections (guard close/connect)
            with self._control_lock:
                if self.control_socket:
                    with contextlib.suppress(Exception):
                        self.control_socket.close()

                self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.control_socket.settimeout(5.0)
                self.control_socket.connect(
                    (self.selected_server["address"], self.control_port)
                )

            # Perform authentication handshake over the connected control socket
            server_info = self.authenticate_with_server(
                (self.selected_server["address"], self.control_port), self.control_socket
            )

            # Update selected_server with returned server_info
            if server_info:
                self.selected_server.update(server_info)

            self.control_connected = True
            # authenticated

            # Connect to data server - close pre-existing connections (guard close)
            if self.data_socket:
                with contextlib.suppress(Exception):
                    self.data_socket.close()

            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.settimeout(5.0)
            self.data_socket.connect((self.selected_server["address"], self.data_port))
            self.data_connected = True

            # data server connected

            # Emit status
            self.status = ClientStatus.SERVER_CONNECTED
            self.server_connected.emit(True)
            # After successful connect, request device discovery asynchronously
            # so we don't block UI. Background worker will emit discovery signals.
            try:
                # Start background discovery (DeviceDiscoverWorker is used by discover_devices)
                self.discover_devices()
            except Exception:
                # Best-effort: don't fail the connection flow if discovery cannot start
                self.log_message.emit(
                    "debug", "Background device discovery could not be started"
                )
        except AuthenticationError as e:
            self.status = ClientStatus.DEFAULT
            self.log_message.emit("error", f"Authentication failed: {e}")
            self.server_connected.emit(False)
        except Exception as e:
            self.status = ClientStatus.DEFAULT
            self.log_message.emit("error", f"Server connection failed: {e}")
            self.server_connected.emit(False)

    def disconnect_from_server(self):
        # Acquire lock while closing control socket to avoid closing under concurrent send/recv
        with self._control_lock:
            if self.control_socket:
                with contextlib.suppress(Exception):
                    self.control_socket.close()
                self.control_socket = None

            if self.data_socket:
                with contextlib.suppress(Exception):
                    self.data_socket.close()
                self.data_socket = None

            self.control_connected = False
            self.data_connected = False

        self.status = ClientStatus.SERVER_DISCONNECTED
        try:
            self.server_disconnected.emit(True)
        except Exception:
            self.server_disconnected.emit()
        self.log_message.emit("info", "Disconnected from server")

    # Public convenience API for UI-driven requests --------------------------------
    def request_disconnect(self):
        """User-requested disconnect: mark as manual to avoid auto-reconnect.

        This wrapper ensures that a disconnect initiated by the UI will not be
        immediately undone by the client's background run-loop trying to
        re-establish the control socket.
        """
        with contextlib.suppress(Exception):
            self._manual_disconnect = True

        # Perform normal disconnect cleanup
        self.disconnect_from_server()

    def request_connect(self, server_info: dict = None):
        """User-requested connect: clear manual-disconnect flag and attempt connection.

        If a server_info dict is provided it becomes the selected server
        before initiating the connection.
        """
        with contextlib.suppress(Exception):
            # Clear manual disconnect so run-loop may reconnect if needed
            self._manual_disconnect = False

        if server_info:
            self.selected_server = server_info

        # Attempt an immediate connection
        with contextlib.suppress(Exception):
            self.connect_to_server()

    ### Device Commands

    ### General functions
    def _send_and_receive(self, command_data: bytes, timeout: float = None) -> bytes:
        """Send command_data and receive response under the control lock.

        This helper temporarily sets socket timeout if requested and restores it.
        """
        with self._control_lock:
            prev_timeout = None
            try:
                if timeout is not None and self.control_socket is not None:
                    prev_timeout = self.control_socket.gettimeout()
                    self.control_socket.settimeout(timeout)

                # removed detailed send trace

                self.control_socket.send(command_data)
                response_data = self.control_socket.recv(MAX_BUFFER_SIZE)
                # removed detailed recv trace
            finally:
                with contextlib.suppress(Exception):
                    if timeout is not None and self.control_socket is not None:
                        self.control_socket.settimeout(prev_timeout)

        return response_data

    def send_control_command(
        self,
        command_type,
        params=None,
        timeout: float = None,
        disconnect_on_error: bool = True,
    ):
        """Send control command to server"""
        if not self.control_connected:
            self.error_occurred.emit("Not connected to control server")
            return None

        if (
            not isinstance(command_type, Command)
            or command_type.name not in SUPPORTED_COMMANDS
        ):
            self.error_occurred.emit(
                f"Invalid command sent: {command_type} \n Supported commands are: {SUPPORTED_COMMANDS}"
            )
            return None

        # Ensure params are JSON-serializable (convert Configuration objects etc.)
        command = {"type": command_type.value, "payload": params or {}}

        try:
            command_data = json.dumps(command).encode("utf-8")
            response_data = self._send_and_receive(command_data, timeout=timeout)

            if not response_data:
                return None

            resp = None
            try:
                resp = json.loads(response_data.decode("utf-8"))
            except Exception:
                self.log_message.emit("error", "Failed to parse control response")

            return resp

        except socket.timeout as e:
            # For long-running ops like discovery, caller may opt to not disconnect on timeout
            self.error_occurred.emit(f"Control communication error: {e}")
            if disconnect_on_error:
                with contextlib.suppress(Exception):
                    self.disconnect_from_server()
            return None
        except Exception as e:
            self.error_occurred.emit(f"Control communication error: {e}")
            # Ensure we close sockets safely (unless caller disabled it)
            if disconnect_on_error:
                with contextlib.suppress(Exception):
                    self.disconnect_from_server()
            return None

    def discover_devices(self):
        # Avoid starting another discovery while one is active
        if self._discovering_devices:
            self.log_message.emit("warn", "Device discovery already in progress")
            return

        self.log_message.emit("info", "Discovering devices...")
        self._discovering_devices = True
        self.device_discovery_started.emit()

        worker = DeviceDiscoverWorker(self)

        def _on_finished(discovered):
            """
            Server response for discovered groups follows the same convention as handler, i.e.
            'group_id': {
                'device_id': DeviceStatus
            }

            Note that the server will only return devices for groups that were requested in the
            discovery command payload. If no groups were specified, all available devices are
            returned, without being formatted into groups.
            """
            # We have groups that are discovered, so we parse accordingly
            for group_id, device_dicts in discovered.items():
                # Ensure group exists in local state map
                if group_id not in self.device_states:
                    self.device_states[group_id] = {}

                for device_id, device_status in device_dicts.items():
                    # Ensure device entry exists with expected keys
                    if device_id not in self.device_states[group_id]:
                        self.device_states[group_id][device_id] = {
                            "group": group_id,
                            "id": device_id,
                            "config": {},
                            "state": DeviceStatus.DISCONNECTED,
                        }

                    # Safely convert status to DeviceStatus enum if possible
                    try:
                        new_state = DeviceStatus(device_status)
                    except Exception:
                        # Fallback to DISCONNECTED for unknown values
                        new_state = DeviceStatus.DISCONNECTED

                    self.device_states[group_id][device_id]["state"] = new_state

            # Emit discovery finished signal with current device states
            self.devices_discovered.emit(self.device_states)
            self._discovering_devices = False

        worker.signals.finished.connect(_on_finished)
        self.thread_pool.start(worker)

    def connect_device(self, device_id=None):
        """Connect to device"""
        if getattr(self, "_discovering_devices", False):
            self.error_occurred.emit(
                "Cannot connect to device while device discovery is in progress"
            )
            return False
        self.log_message.emit("info", "Connecting all discovered devices...")
        # Request server to connect all initialized devices; no per-device payload
        # Use a longer timeout for potentially slow device connect operations
        response = self.send_control_command(
            Command.CONNECT_DEVICES, timeout=30.0, disconnect_on_error=False
        )
        # Handle new device lifecycle response types
        if response and isinstance(response, dict):
            rtype = response.get("type")
            payload = response.get("payload", {})

            devices = payload.get("devices", {})
            for device_id in devices:
                for group_id, group in self.device_states.items():
                    if device_id not in group:
                        continue

                    match rtype:
                        case Response.DEVICE_CONNECTING.value:
                            self.device_states[group_id][device_id][
                                "state"
                            ] = DeviceStatus.CONNECTING
                            self.device_connected.emit(device_id, False)

                        case Response.DEVICE_CONNECTED.value:
                            self.device_states[group_id][device_id][
                                "state"
                            ] = DeviceStatus.CONNECTED
                            self.device_connected.emit(device_id, True)

                        case Response.DEVICE_DISCONNECTED.value:
                            self.device_states[group_id][device_id][
                                "state"
                            ] = DeviceStatus.DISCONNECTED
                            self.device_disconnected.emit(device_id, True)

                    break

            return True

        # If we reach here, it's an error
        if response and isinstance(response, dict):
            payload = response.get("payload", {})
            error_msg = payload.get("message", "Unknown error")
        else:
            error_msg = "No response"

        self.error_occurred.emit(f"Device connection failed: {error_msg}")
        self.device_connection_failed.emit(device_id)
        return False

    def disconnect_device(self):
        """Disconnect from device"""
        self.log_message.emit("info", "Disconnecting device...")

        # Stop streaming first
        if self.status == ClientStatus.STREAMING:
            self.stop_streaming()

        response = self.send_control_command(Command.DISCONNECT_DEVICES)

        if response and response.get("type") == Response.SUCCESS.value:
            self.log_message.emit("info", "Device disconnected")
            self.device_disconnected.emit()
            return True
        else:
            if response and isinstance(response, dict):
                payload = (
                    response.get("payload", {}) if isinstance(response, dict) else {}
                )
                error_msg = payload.get("message", "Unknown error")
            else:
                error_msg = "No response"
            self.error_occurred.emit(f"Disconnect failed: {error_msg}")
            return False

    def start_streaming(self):
        """Start real-time data streaming (device-level streaming).

        Sends a START command over the control channel and updates client state.
        The background run loop will attempt to connect the data socket if needed.
        """
        if getattr(self, "_discovering_devices", False):
            self.error_occurred.emit(
                "Cannot start streaming while device discovery is in progress"
            )
            return False

        self.log_message.emit("info", "Starting data streaming...")
        response = self.send_control_command(Command.START_STREAMING)

        if response and response.get("type") == Response.SUCCESS.value:
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
                        self.data_socket.settimeout(5.0)
                        self.data_socket.connect(
                            (self.selected_server.get("address"), self.data_port)
                        )
                    self.data_connected = True
            except Exception as e:
                self.log_message.emit("error", f"Data connection failed: {e}")

            self.streaming_started.emit(True)
            return True
        else:
            if response and isinstance(response, dict):
                payload = (
                    response.get("payload", {}) if isinstance(response, dict) else {}
                )
                error_msg = payload.get("message", "Unknown error")
            else:
                error_msg = "No response"
            self.error_occurred.emit(f"Failed to start streaming: {error_msg}")
            return False

    def stop_streaming(self):
        """Stop data streaming from device(s)."""
        if getattr(self, "_discovering_devices", False):
            self.error_occurred.emit(
                "Cannot stop streaming while device discovery is in progress"
            )
            return False

        self.log_message.emit("info", "Stopping data streaming...")

        response = self.send_control_command(Command.STOP_STREAMING)

        # Close/cleanup data socket regardless of control response
        if self.data_socket:
            with contextlib.suppress(Exception):
                self.data_socket.close()
            self.data_socket = None
            self.data_connected = False

        # Update status back to connected server
        self.status = ClientStatus.SERVER_CONNECTED

        if response and response.get("type") == Response.SUCCESS.value:
            self.log_message.emit("info", "Data streaming stopped")
            self.streaming_stopped.emit(True)
            return True
        else:
            if response and isinstance(response, dict):
                payload = (
                    response.get("payload", {}) if isinstance(response, dict) else {}
                )
                error_msg = payload.get("message", "Unknown error")
            else:
                error_msg = "No response"
            self.error_occurred.emit(f"Failed to stop streaming: {error_msg}")
            return False

    def configure_device(self, device_id, config):
        """Configure device parameters"""
        if getattr(self, "_discovering_devices", False):
            self.error_occurred.emit(
                "Cannot configure device while device discovery is in progress"
            )
            return False

        self.log_message.emit("info", "Configuring device: {device_id}")
        response = self.send_control_command(
            Command.UPDATE_RUNNING_PARAMETER, {"id": device_id, "config": config}
        )

        if response and response.get("type") == Response.SUCCESS.value:
            self.log_message.emit("info", "Device configured successfully")
            return True
        else:
            if response and isinstance(response, dict):
                payload = (
                    response.get("payload", {}) if isinstance(response, dict) else {}
                )
                error_msg = payload.get("message", "Unknown error")
            else:
                error_msg = "No response"
            self.error_occurred.emit(f"Configuration failed: {error_msg}")
            return False

    def update_params(self, config):
        pass

    # Client function for PyQt loops
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

    ### Helpers
    def get_display_sources(self):
        pass

    def update_device_state(self, device_id) -> bool:
        """Helper function to keep track of device states internally"""
        pass


class DataStreamer(QThread):
    log_message = pyqtSignal(str, str)
    data_received = pyqtSignal(np.ndarray)

    def __init__(self, running, parent=None):
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

                data_length = struct.unpack("!I", length_data)[0]

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
                if self.status == ClientStatus.STREAMING:
                    self.log_message.emit("error", f"Data receiving error: {e}")
                return

        self.log_message.emit("info", "Data receiving thread stopped")

    def _recv_exactly(self, num_bytes):
        """Receive exactly num_bytes from data socket"""
        data = b""
        while len(data) < num_bytes:
            try:
                chunk = self.data_socket.recv(num_bytes - len(data))
                if not chunk:
                    return None
                data += chunk
            except Exception as e:
                self.log_message.emit("error", f"Receiving error: {e}")
                return None
        return data

    def _deserialize_data(self, data_bytes):
        """Deserialize numpy data from server"""
        try:
            # Read header length
            header_length = struct.unpack("!I", data_bytes[:4])[0]

            # Read header
            header_bytes = data_bytes[4 : 4 + header_length]
            header = json.loads(header_bytes.decode("utf-8"))

            # Read data
            array_bytes = data_bytes[4 + header_length :]

            # Reconstruct numpy array
            shape = tuple(header["shape"])
            dtype = np.dtype(header["dtype"])

            data = np.frombuffer(array_bytes, dtype=dtype).reshape(shape)

            return data

        except Exception as e:
            self.log_message.emit("error", f"Data deserialization error: {e}")
            return None

    def stop(self):
        self.running = False

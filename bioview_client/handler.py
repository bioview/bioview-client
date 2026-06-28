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
import os
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
    get_challenge_response,
    get_unique_path,
    is_dict_of_dicts,
    parse_and_validate_response,
    send_command,
)
from PyQt6.QtCore import QThread, QThreadPool, pyqtSignal

from bioview_client.workers import DataSaver, DataStreamer, DeviceInitWorker, FunctionWorker, ScanWorker


def _sanitize_label(label: str) -> str:
    """Make a routine label safe for use in a file name."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(label)).strip("_")
    return safe or "routine"


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

    # Data signal for graphical output. Carries (data, sources) where data is a
    # (num_sources, num_samples) array and sources is the ordered list of source
    # descriptor dicts describing each row.
    data_received = pyqtSignal(np.ndarray, object)

    def __init__(
        self,
        config: Configuration = None,
        experiment_config=None,
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

        # Client-side saving (fast disk on the client per design). Settings are
        # populated from the UI; the saver thread is created when streaming starts.
        self.data_saver = None
        self.enable_save = False
        self.save_dir = ""
        self.file_name = ""
        # Optional routine label appended to the file name for timed-mode runs:
        #   timed:   <file_name>_<label>.bvr
        #   untimed: <file_name>.bvr
        self.save_label = None

        # Thread pool for control/device operations (connect, init, start/stop).
        # Kept small since these are serialized through the control socket lock.
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(8)

        # Separate, bounded pool for the LAN scan so probing up to 254 hosts does
        # not spawn a 254-thread storm that contends with the rest of the app.
        self.scan_pool = QThreadPool()
        self.scan_pool.setMaxThreadCount(32)
        self._cancel_scan = False

        # Guard so overlapping auto-connect attempts (e.g. fast localhost probe and
        # a full LAN autoconnect firing close together) never run concurrently.
        self._connecting = False
        self._connect_guard_lock = threading.Lock()

        # Device discovery state
        self._discovering_devices = False

        # Lock to serialize control (and related socket) operations to avoid races
        # between send/recv and close operations from different threads.
        self._control_lock = threading.Lock()

        # Running state for the QThread
        self.running = False

        # Keep track of unified configuration. The monitor UI passes a separate
        # experiment_config object and a group_configs dict (device_id -> config
        # object); merge them into a single Configuration here. A pre-built
        # Configuration (used by other frontends) takes precedence if provided.
        if config is None and (experiment_config is not None or group_configs):
            config = self._build_configuration(experiment_config, group_configs)
        self.config = config or Configuration()

        # Backward compatibility for existing code that uses group_configs
        self.group_configs = self.config.to_dict()

        # Now, while we understand devices in the form of groups, we keep track of
        # states for individual devices for clearer presentation in the UI
        self.device_states = {}
        for device_id, device_cfg in self.config.devices.items():
            self.device_states[device_id] = {
                "id": device_id,
                "config": device_cfg.to_dict(),
                "status": DeviceStatus.DISCONNECTED,
            }

        # Seed save settings from the experiment configuration if present
        if self.config.experiment is not None:
            self.enable_save = bool(self.config.experiment.get_param("enable_save", False))
            self.save_dir = self.config.experiment.get_param("save_dir", "") or ""
            self.file_name = self.config.experiment.get_param("file_name", "") or ""

    # Save configuration setters (driven by the UI)
    def set_save_enabled(self, enabled: bool):
        self.enable_save = bool(enabled)

    def set_save_param(self, name: str, value):
        if name == "save_dir":
            self.save_dir = value or ""
        elif name == "file_name":
            self.file_name = value or ""

    def set_save_label(self, label):
        """Set (or clear with None) the routine label appended to recordings made
        in timed mode."""
        self.save_label = label or None

    def record_param_change(self, device_id: str, param: str, value):
        """Record a UI-driven device parameter tweak. Keeps our config snapshot
        current (so a subsequent recording's start metadata is accurate) and, when
        a recording is active, logs the change with a timestamp into the .bvr."""
        with contextlib.suppress(Exception):
            self.config.update_device_param(device_id, param, value)
        if self.data_saver is not None:
            self.data_saver.record_change(device_id, param, value)

    @staticmethod
    def _build_configuration(experiment_config, group_configs) -> Configuration:
        """Merge a separate experiment config and per-device group configs into a
        single unified Configuration."""
        config_dict = {}

        if experiment_config is not None:
            config_dict["experiment"] = (
                experiment_config.to_dict()
                if hasattr(experiment_config, "to_dict")
                else experiment_config
            )

        if group_configs:
            for device_id, device_cfg in group_configs.items():
                config_dict[device_id] = (
                    device_cfg.to_dict()
                    if hasattr(device_cfg, "to_dict")
                    else device_cfg
                )

        return Configuration.from_dict(config_dict)

    # Thread handling
    def _send_command_locked(self, command, params=None):
        with self._control_lock:
            if not self.control_socket:
                return None
            return send_command(self.control_socket, command, params)

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
        # Guard against overlapping scans (which would corrupt shared counters
        # and make completion fire unreliably). A scan in progress is left to run.
        if self.status == ClientStatus.SCANNING:
            self.log_message.emit("debug", "Server scan already in progress")
            return

        self.status = ClientStatus.SCANNING
        self.discovered_servers = []
        self._cancel_scan = False

        # Probe every host on the local /24 plus loopback, so a local-only server
        # is always reachable even if NIC detection is off or there is no LAN.
        targets = [f"{self.network_prefix}.{i}" for i in range(1, 255)]
        if "127.0.0.1" not in targets:
            targets.append("127.0.0.1")
        total = len(targets)

        # All scan state is local to this invocation so concurrent/rapid rescans
        # never clobber each other's counters.
        scan_lock = threading.Lock()
        state = {"completed": 0, "done": False, "last_update": time.time()}

        def handle_result(found):
            with scan_lock:
                if self._cancel_scan or state["done"]:
                    return

                state["completed"] += 1

                # Collect a discovered server (deduplicated by IP)
                if found and isinstance(found, dict):
                    addr = found.get("ip")
                    if addr and not any(
                        s.get("ip") == addr for s in self.discovered_servers
                    ):
                        self.discovered_servers.append(found)

                completed = state["completed"]
                is_done = completed >= total
                if is_done:
                    state["done"] = True

                now = time.time()
                emit_progress = (now - state["last_update"] >= 0.1) or is_done
                if emit_progress:
                    state["last_update"] = now

            # Emit signals outside the lock to avoid holding it across Qt dispatch
            if emit_progress:
                self.server_scan_progress.emit(int((completed / total) * 100))

            if is_done:
                self.status = ClientStatus.DEFAULT
                self.server_scan_completed.emit(self.discovered_servers)

        for target_ip in targets:
            if self._cancel_scan:
                break
            worker = ScanWorker(target_ip, self.control_port)
            worker.signals.result.connect(handle_result)
            self.scan_pool.start(worker)

    def cancel_scan(self):
        self._cancel_scan = True
        self.scan_pool.clear()

        # Reset out of the scanning state so a fresh scan can be started
        if self.status == ClientStatus.SCANNING:
            self.status = ClientStatus.DEFAULT

        self.server_scan_completed.emit(self.discovered_servers)

    def quick_connect_localhost(self):
        """Fast path for seamless localhost usage: probe 127.0.0.1 with a short
        timeout and, if a server answers, register it and connect immediately --
        without the full LAN scan and without blocking the UI thread."""
        if self.status >= ClientStatus.SERVER_CONNECTED:
            return
        if self._connecting:
            return
        worker = ScanWorker("127.0.0.1", self.control_port, timeout=0.5)
        worker.signals.result.connect(self._on_localhost_probe)
        self.scan_pool.start(worker)

    def _on_localhost_probe(self, found):
        # Runs on the UI thread (queued from the worker). Bail unless we found a
        # local server and are still unconnected.
        if not found or not isinstance(found, dict):
            return
        if self.status >= ClientStatus.SERVER_CONNECTED or self._connecting:
            return

        found.setdefault("ip", "127.0.0.1")
        self.discovered_servers = [found] + [
            s for s in self.discovered_servers if s.get("ip") != found.get("ip")
        ]
        # Surface the localhost server in the UI's server dropdown
        self.server_scan_completed.emit(self.discovered_servers)

        self.selected_server = found
        self.log_message.emit("info", "Localhost server found; connecting...")
        self.connect_to_server()

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
            challenge = None
            if resp_type == Response.SERVER_CHALLENGE.name and resp_payload:
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
                server_info = auth_resp_payload.get("server_info", None) if auth_resp_payload else None

                # Update status
                self.status = ClientStatus.SERVER_CONNECTED

                hostname = server_info.get("hostname") if server_info else "server"
                self.log_message.emit(
                    "info", f"Successfully connected to {hostname}"
                )
            else:
                err = auth_resp_payload.get("message", "") if auth_resp_payload else ""
                raise AuthenticationError(f"Server authentication failed: {err}")
        except Exception as e: 
            self.log_message.emit("error", f"Authentication with server failed: {e}")
            server_info = None

        return server_info

    def change_selected_server(self, index: int):
        if self.discovered_servers is None or len(self.discovered_servers) == 0:
            self.selected_server = {}
        elif index < 0 or index >= len(self.discovered_servers):
            self.selected_server = self.discovered_servers[0]
        else:
            self.selected_server = self.discovered_servers[index]

    def connect_to_server(self):
        """Dispatch the (blocking) connection handshake onto the thread pool so the
        UI thread is never blocked while sockets connect and authenticate."""
        self.thread_pool.start(FunctionWorker(self._connect_to_server_impl))

    def _connect_to_server_impl(self):
        # Serialize connection attempts: if one is already in flight, skip this one
        # so the fast localhost path and a LAN autoconnect can't both connect.
        with self._connect_guard_lock:
            if self._connecting:
                return
            self._connecting = True

        try:
            self._do_connect_to_server()
        finally:
            with self._connect_guard_lock:
                self._connecting = False

    def _do_connect_to_server(self):
        # Pick a server if none has been explicitly selected
        if not self.selected_server:
            if len(self.discovered_servers) == 0:
                self.log_message.emit("error", "No valid servers available.")
                return
            self.selected_server = self.discovered_servers[0]
            self.log_message.emit(
                "info", f"Connecting to server: {self.selected_server.get('ip')}"
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

            # Authentication failed - do not enter a connected state
            if not server_info:
                raise AuthenticationError("Authentication with server failed")

            # Connect data socket - close pre-existing connections
            if self.data_socket:
                with contextlib.suppress(Exception):
                    self.data_socket.close()

            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.settimeout(5)
            self.data_socket.connect((self.selected_server["ip"], self.data_port))
            self.data_connected = True

            # Start the long-lived data receiver for the whole session. Streaming
            # start/stop only toggles whether the server sends; the socket and this
            # receiver stay up until disconnect.
            self._start_data_streamer()

            # Update server info and data sources
            self.selected_server.update(server_info)
            self.data_sources = server_info.get("data_sources", None)

            # Once everything succeeds, update the status
            self.status = ClientStatus.SERVER_CONNECTED
            self.server_connected.emit(True)
        except Exception as e:
            # Reset status
            self.status = ClientStatus.SERVER_DISCONNECTED

            # Stop the receiver (if any) and reset sockets
            self._stop_data_streamer()
            with contextlib.suppress(Exception):
                if self.control_socket:
                    self.control_socket.close()
            self.control_socket = None
            with contextlib.suppress(Exception):
                if self.data_socket:
                    self.data_socket.close()
            self.data_socket = None
            self.data_connected = False

            # Log message in UI and notify listeners of failure
            self.log_message.emit("error", f"Server connection failed: {e}")
            self.server_disconnected.emit(True)

    def disconnect_from_server(self):
        # Stop the long-lived data receiver before closing the socket it reads
        self._stop_data_streamer()

        # Stop any client-side saving in progress
        if self.data_saver is not None:
            with contextlib.suppress(Exception):
                self.data_saver.stop_saving()
            self.data_saver = None

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

        self.data_connected = False
        self.status = ClientStatus.SERVER_DISCONNECTED

        try:
            self.server_disconnected.emit(True)
        except Exception:
            self.server_disconnected.emit()

        self.log_message.emit("info", "Disconnected from server")

    ### Device Commands
    def discover_devices(self):
        self.initialize_devices(only_discover=True)
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
            The server reports device states as a flat mapping:
                { device_id (== group_id): DeviceStatus value }

            The response only contains keys for device groups that were requested
            in the provided payload.
            """
            if not group_status_dict or not isinstance(group_status_dict, dict):
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

        response = self._send_command_locked( command=Command.DISCONNECT_DEVICES
        )
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.log_message.emit("info", "Devices disconnected")
            self.device_disconnect_succeeded.emit()
        else:
            msg = resp_payload.get("message", "")
            self.log_message.emit("error", f"Disconnect failed: {msg}")

    # Data receiver lifecycle (long-lived for the whole session)
    def _start_data_streamer(self):
        """Start the long-lived data receiver bound to the session data socket.
        Idempotent: any previous receiver is stopped first."""
        if self.data_socket is None:
            return
        self._stop_data_streamer()
        self.data_streamer = DataStreamer(data_conn=self.data_socket)
        self.data_streamer.data_received.connect(self._handle_received_data)
        self.data_streamer.log_message.connect(self.log_message)
        self.data_streamer.start()

    def _stop_data_streamer(self):
        if self.data_streamer is not None:
            with contextlib.suppress(Exception):
                self.data_streamer.stop()
                # Give the receive loop time to exit its (timed) recv cleanly
                self.data_streamer.wait(2000)
            self.data_streamer = None

    # Data streaming handlers
    def start_streaming(self):
        """Dispatch the streaming start (blocking control RPC + socket setup) onto
        the thread pool to keep the UI responsive."""
        self.thread_pool.start(FunctionWorker(self._start_streaming_impl))

    def _start_streaming_impl(self):
        if self._discovering_devices:
            self.log_message.emit(
                "warning", "Cannot start streaming while device discovery is in progress"
            )
            return False

        if not self.control_socket:
            self.log_message.emit("error", "Cannot start streaming: not connected to a server")
            return False

        self.log_message.emit("info", "Attempting to start data streaming...")
        self.control_socket.settimeout(10)
        response = self._send_command_locked(
            command=Command.START_STREAMING,
            params=self.config.to_dict(),
        )
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.status = ClientStatus.STREAMING
            self.log_message.emit("info", "Data streaming started")

            # The data socket is opened once per session at connect time and the
            # server keeps a single data connection for the whole session. We must
            # NOT tear it down on stop/restart (doing so left the server sending on
            # a dead socket -> "connection reset by peer"). Just make sure the
            # long-lived receiver is running on the existing socket.
            if not self.data_connected:
                self.log_message.emit(
                    "warning",
                    "Data connection not established; reconnect to the server.",
                )
            elif self.data_streamer is None or not self.data_streamer.isRunning():
                self._start_data_streamer()

            # Set up client-side saving (full-rate) if enabled
            self._start_saving()

            # Emit success
            self.streaming_started.emit(True)
            self.log_message.emit("debug", "Streaming started successfully")
        else:
            msg = resp_payload.get("message", "") if resp_payload else ""
            self.log_message.emit("error", f"Failed to start streaming: {msg}")

    def _start_saving(self):
        """Create and start the client-side disk writer if saving is enabled.

        File naming:
            timed mode:   <file_name>_<mode label>.bvr
            unlimited:    <file_name>.bvr
        Duplicate names are de-duplicated with a numeric suffix.
        """
        self.data_saver = None
        if not self.enable_save:
            return

        save_dir = self.save_dir or os.getcwd()
        base = os.path.splitext((self.file_name or "").strip())[0] or "bioview_recording"
        if self.save_label:
            base = f"{base}_{_sanitize_label(self.save_label)}"
        file_name = f"{base}.bvr"

        try:
            save_path = get_unique_path(save_dir, file_name)
            sources = self.data_sources or []
            # Snapshot the device configuration constants at recording start
            device_config = {}
            if self.config is not None:
                device_config = {
                    dev_id: cfg.to_dict()
                    for dev_id, cfg in self.config.devices.items()
                }
            self.data_saver = DataSaver(
                save_path=save_path,
                sources=sources,
                device_config=device_config,
                log_signal=self.log_message,
            )
            self.data_saver.start_saving()
        except Exception as e:
            self.data_saver = None
            self.log_message.emit("error", f"Unable to start saving: {e}")

    def _handle_received_data(self, data, sources=None):
        # Tee the full-rate chunk: one branch to disk, one to the UI for display.
        # Use the per-chunk source list when present (more robust for multi-device),
        # falling back to the data sources advertised at connection time.
        if self.data_saver is not None:
            self.data_saver.add(data)

        chunk_sources = sources if sources else self.data_sources
        self.data_received.emit(data, chunk_sources)

    def stop_streaming(self):
        """Dispatch the streaming stop onto the thread pool to keep the UI responsive."""
        self.thread_pool.start(FunctionWorker(self._stop_streaming_impl))

    def _stop_streaming_impl(self):
        if self._discovering_devices:
            self.log_message.emit(
                "warning", "Cannot stop streaming while device discovery is in progress"
            )
            return False

        self.log_message.emit("debug", "Attempting to stop streaming...")

        if self.control_socket:
            self.control_socket.settimeout(10)
        response = self._send_command_locked( command=Command.STOP_STREAMING)
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.ERROR.name:
            err = resp_payload.get("message", "") if resp_payload else ""
            msg = f"Failed to stop streaming: {err}"
            self.log_message.emit("error", msg)

        # Intentionally keep the data socket AND the receiver alive for the whole
        # session. The server pauses its backends on stop (it stops sending) but
        # keeps the same per-session data connection, so closing it here is what
        # previously broke restart. The receiver simply idles until data resumes.

        # Stop client-side saving and flush to disk
        if self.data_saver is not None:
            self.data_saver.stop_saving()
            self.data_saver = None

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
        response = self._send_command_locked(
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

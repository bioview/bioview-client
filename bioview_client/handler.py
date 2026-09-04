"""Front-end agnostic client handler. See bioview-docs/architecture/client.md."""

import contextlib
import os
import select
import socket
import threading
import time
from typing import Any

import numpy as np
from bioview_common import (
    AUTH_TIMEOUT,
    CONTROL_PORT,
    DATA_PORT,
    DEVICE_OP_COMMAND_TIMEOUT,
    RESPONSE_TIMEOUT,
    STREAMING_COMMAND_TIMEOUT,
    AuthenticationError,
    ClientStatus,
    Command,
    Configuration,
    DeviceStatus,
    Response,
    describe_failure,
    get_app_info,
    get_challenge_response,
    get_ip,
    get_unique_path,
    parse_and_validate_response,
    send_command,
)
from PyQt6.QtCore import QThread, QThreadPool, pyqtSignal

from bioview_client.workers import (
    DataSaver,
    DataStreamer,
    DeviceInitWorker,
    FunctionWorker,
    ScanWorker,
)


# The device-status poll repeats every couple of seconds and would bury
# everything else in the trace; the poll loop reports its own progress.
_UNTRACED_COMMANDS = {"GET_DEVICE_STATUS"}

# Longest single value rendered in a debug trace line.
_TRACE_VALUE_LIMIT = 120

# How often the handler thread checks that the control connection is still up.
CONNECTION_CHECK_INTERVAL = 2.0


def _describe_params(params) -> str:
    """A one-line summary of a command's parameters for the debug trace."""
    if not params:
        return ""

    parts = []
    for key, value in params.items():
        if isinstance(value, dict):
            summary = f"{{{len(value)} key(s): {', '.join(list(value)[:4])}}}"
        elif isinstance(value, list | tuple | set):
            summary = f"[{len(value)} item(s)]"
        else:
            summary = str(value)
        if len(summary) > _TRACE_VALUE_LIMIT:
            summary = summary[:_TRACE_VALUE_LIMIT] + "..."
        parts.append(f"{key}={summary}")

    return " (" + ", ".join(parts) + ")"


def _describe_response(response) -> str:
    """A one-line summary of a raw response, for the debug trace."""
    if response is None:
        return "no response (timed out or connection lost)"

    resp_type, payload = parse_and_validate_response(response)
    if resp_type is None:
        return f"unreadable response ({len(response)} bytes)"

    detail = ""
    if payload:
        message = payload.get("message")
        detail = f" -- {message}" if message else f" ({', '.join(list(payload)[:5])})"
    return f"{resp_type}{detail}"


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

    # Device control signals (success only; failures are logged, not signalled)
    devices_discovered = pyqtSignal(dict)
    # (devices, backends), where backends maps device_type -> editable schema
    devices_listed = pyqtSignal(list, dict)
    device_list_failed = pyqtSignal(str)
    device_config_updated = pyqtSignal(dict, str)
    device_config_failed = pyqtSignal(str)
    device_init_succeeded = pyqtSignal(dict)
    device_init_failed = pyqtSignal()
    device_status_updated = pyqtSignal(dict)
    device_disconnect_succeeded = pyqtSignal()
    # Emitted when the server's advertised source list changes
    data_sources_changed = pyqtSignal(list)

    # Streaming states
    streaming_started = pyqtSignal(bool)
    streaming_stopped = pyqtSignal(bool)

    # General info signals
    log_message = pyqtSignal(str, str)

    # (data, sources): a (num_sources, num_samples) array plus one source
    # descriptor dict per row, in the same order
    data_received = pyqtSignal(np.ndarray, object)

    def __init__(
        self,
        config: Configuration = None,
        experiment_config=None,
        group_configs: dict = None,
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

        self.discovered_servers: list[dict] = []
        self.selected_server: dict = {}

        self.data_port: int = data_port
        self.control_port: int = control_port

        self.data_thread = None
        self.control_thread = None

        self.data_socket = None
        self.control_socket = None

        self.status = ClientStatus.DEFAULT
        self.data_connected = False

        self.data_sources = None
        # Why each device group failed, as reported by the server
        self.device_errors = {}
        self.data_streamer = None

        # Client-side saving; the saver thread is created when streaming starts
        self.data_saver = None
        self.enable_save = False
        self.save_dir = ""
        self.file_name = ""
        # Timed runs save as <file_name>_<label>.bvr, untimed as <file_name>.bvr
        self.save_label = None

        # Control/device operations, serialized through the control socket lock
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(8)

        # Bounded separately so a 254-host LAN scan cannot starve the app
        self.scan_pool = QThreadPool()
        self.scan_pool.setMaxThreadCount(32)
        self._cancel_scan = False

        # Keeps the fast localhost probe and a LAN autoconnect from racing
        self._connecting = False
        self._connect_guard_lock = threading.Lock()

        # Device discovery state
        self._discovering_devices = False

        # One streaming start at a time; see start_streaming().
        self._start_streaming_lock = threading.Lock()
        self._start_streaming_pending = False

        # Serializes send/recv against close across threads
        self._control_lock = threading.Lock()

        # Running state for the QThread
        self.running = False

        # A pre-built Configuration wins; otherwise experiment + group configs
        # are merged into one.
        if config is None and (experiment_config is not None or group_configs):
            config = self._build_configuration(experiment_config, group_configs)
        self.config = config or Configuration()

        # Backward compatibility for existing code that uses group_configs
        self.group_configs = self.config.to_dict()

        # Devices are driven as groups but tracked individually for the UI
        self.device_states = {}
        for device_id, device_cfg in self.config.devices.items():
            self.device_states[device_id] = {
                "id": device_id,
                "config": device_cfg.to_dict(),
                "status": DeviceStatus.DISCONNECTED,
            }

        # Seed save settings from the experiment configuration if present
        if self.config.experiment is not None:
            self.enable_save = bool(
                self.config.experiment.get_param("enable_save", False)
            )
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
        """Set (or clear with None) the routine label appended to recordings."""
        self.save_label = label or None

    def record_param_change(self, device_id: str, param: str, value):
        """Record a UI-driven device parameter tweak. Keeps our config snapshot
        current (so a subsequent recording's start metadata is accurate) and, when
        a recording is active, logs the change with a timestamp into the .bvr."""
        with contextlib.suppress(Exception):
            self.config.update_device_param(device_id, param, value)
        if self.data_saver is not None:
            self.data_saver.record_change(device_id, param, value)

    def has_valid_save_target(self) -> bool:
        """Whether both a file name and a save folder have been provided."""
        return bool((self.file_name or "").strip()) and bool(
            (self.save_dir or "").strip()
        )

    def is_recording(self) -> bool:
        """Whether a client-side recording is currently active."""
        return self.data_saver is not None

    def record_annotation(self, text: str) -> bool:
        """Store an annotation under the recording's ``Annotations`` metadata."""
        if self.data_saver is None:
            return False
        self.data_saver.record_annotation(text)
        return True

    @staticmethod
    def _build_configuration(experiment_config, group_configs) -> Configuration:
        """Merge experiment and per-device group configs into one Configuration."""
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
    def _send_command_locked(self, command, params=None, timeout=None):
        """Send one command and read its reply, serialized against other senders.

        ``timeout`` overrides the socket timeout for this exchange only.
        """
        with self._control_lock:
            if not self.control_socket:
                self.log_message.emit(
                    "debug", f"{command.name} not sent: no control connection"
                )
                return None

            previous = None
            if timeout is not None:
                with contextlib.suppress(Exception):
                    previous = self.control_socket.gettimeout()
                    self.control_socket.settimeout(timeout)

            traced = command.name not in _UNTRACED_COMMANDS
            started = time.time()
            if traced:
                self.log_message.emit(
                    "debug", f"-> {command.name}{_describe_params(params)}"
                )
            try:
                response = send_command(self.control_socket, command, params)
            except Exception as e:
                self.log_message.emit(
                    "debug",
                    f"<- {command.name} raised after "
                    f"{(time.time() - started) * 1000:.0f} ms: {e}",
                )
                raise
            finally:
                if previous is not None:
                    with contextlib.suppress(Exception):
                        self.control_socket.settimeout(previous)

            if traced:
                elapsed_ms = (time.time() - started) * 1000
                self.log_message.emit(
                    "debug",
                    f"<- {command.name}: {_describe_response(response)} "
                    f"in {elapsed_ms:.0f} ms",
                )
            return response

    def run(self):
        self.log_message.emit("info", "Starting client handler...")

        while self.running:
            try:
                if not isinstance(self.status, ClientStatus):
                    self.status = ClientStatus.DEFAULT

                # Without this poll a dropped server is only noticed on the
                # next command, and nothing clears the connected state.
                if (
                    self.status >= ClientStatus.SERVER_CONNECTED
                    and self._connection_dropped()
                ):
                    self.log_message.emit("warning", "Lost connection to server")
                    self.disconnect_from_server()
            except (OSError, ConnectionResetError, BrokenPipeError):
                self.disconnect_from_server()
            finally:
                time.sleep(CONNECTION_CHECK_INTERVAL)

    def _connection_dropped(self) -> bool:
        """True when the control socket has been closed by the far end.

        The lock is taken without blocking, so this never peeks at a reply
        another thread is receiving; a busy socket is by definition alive.
        """
        sock = self.control_socket
        if sock is None:
            return True

        if not self._control_lock.acquire(blocking=False):
            return False
        try:
            if self.control_socket is None:
                return True
            ready, _, _ = select.select([self.control_socket], [], [], 0)
            if not ready:
                return False
            return self.control_socket.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return True
        finally:
            self._control_lock.release()

    # Discover servers in parallel
    def discover_servers(self):
        if self.status == ClientStatus.SCANNING:
            self.log_message.emit("debug", "Server scan already in progress")
            return

        self.status = ClientStatus.SCANNING
        self.discovered_servers = []
        self._cancel_scan = False

        # Loopback is probed explicitly so a local-only server is found even
        # with no LAN or failed NIC detection.
        targets = [f"{self.network_prefix}.{i}" for i in range(1, 255)]
        if "127.0.0.1" not in targets:
            targets.append("127.0.0.1")
        total = len(targets)

        # Scan state is per-invocation so rapid rescans cannot clobber it.
        scan_lock = threading.Lock()
        state = {"completed": 0, "done": False, "last_update": time.time()}

        def handle_result(found):
            self._record_scan_result(found, state, scan_lock, total)

        for target_ip in targets:
            if self._cancel_scan:
                break
            worker = ScanWorker(target_ip, self.control_port)
            worker.signals.result.connect(handle_result)
            self.scan_pool.start(worker)

    def _add_discovered_server(self, found):
        """Record a probe hit, deduplicated by IP."""
        if not found or not isinstance(found, dict):
            return
        addr = found.get("ip")
        if not addr:
            return
        if any(s.get("ip") == addr for s in self.discovered_servers):
            return
        self.discovered_servers.append(found)

    def _record_scan_result(self, found, state, scan_lock, total):
        """Account for one finished probe and emit progress/completion.

        ``state`` and ``scan_lock`` belong to a single discover_servers() call,
        so concurrent or rapid rescans never share counters.
        """
        with scan_lock:
            if self._cancel_scan or state["done"]:
                return

            state["completed"] += 1
            self._add_discovered_server(found)

            completed = state["completed"]
            is_done = completed >= total
            if is_done:
                state["done"] = True

            now = time.time()
            emit_progress = (now - state["last_update"] >= 0.1) or is_done
            if emit_progress:
                state["last_update"] = now

        # Emitted outside the lock: never hold it across a Qt dispatch.
        if emit_progress:
            self.server_scan_progress.emit(int((completed / total) * 100))

        if is_done:
            self.status = ClientStatus.DEFAULT
            self.server_scan_completed.emit(self.discovered_servers)

    def cancel_scan(self):
        self._cancel_scan = True
        self.scan_pool.clear()

        if self.status == ClientStatus.SCANNING:
            self.status = ClientStatus.DEFAULT

        self.server_scan_completed.emit(self.discovered_servers)

    def quick_connect_localhost(self):
        """Probe 127.0.0.1 with a short timeout and connect if a server answers."""
        if self.status >= ClientStatus.SERVER_CONNECTED:
            return
        if self._connecting:
            return
        worker = ScanWorker("127.0.0.1", self.control_port, timeout=0.5)
        worker.signals.result.connect(self._on_localhost_probe)
        self.scan_pool.start(worker)

    def _on_localhost_probe(self, found):
        if not found or not isinstance(found, dict):
            return
        if self.status >= ClientStatus.SERVER_CONNECTED or self._connecting:
            return

        # Keep using loopback: the advertised NIC address may be unroutable
        # from here, or refused outright by a --local server.
        found["ip"] = "127.0.0.1"
        self.discovered_servers = [found] + [
            s for s in self.discovered_servers if s.get("ip") != found.get("ip")
        ]
        self.server_scan_completed.emit(self.discovered_servers)

        self.selected_server = found
        self.log_message.emit("info", "Localhost server found; connecting...")
        self.connect_to_server()

    def _authenticate_with_server(self, server_socket: socket.socket) -> dict[str, Any]:
        """Run the challenge/response handshake over an open control socket."""
        server_socket.settimeout(self.auth_timeout)
        server_info = None

        try:
            response = send_command(
                sock=server_socket,
                command=Command.CONNECT_SERVER,
                params={"client_info": self.info, "timestamp": time.time()},
            )

            resp_type, resp_payload = parse_and_validate_response(response)

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

            auth_resp_type, auth_resp_payload = parse_and_validate_response(
                auth_response
            )

            if auth_resp_type == Response.AUTHENTICATION_SUCCESS.name:
                server_info = (
                    auth_resp_payload.get("server_info", None)
                    if auth_resp_payload
                    else None
                )

                self.status = ClientStatus.SERVER_CONNECTED

                hostname = server_info.get("hostname") if server_info else "server"
                self.log_message.emit("info", f"Successfully connected to {hostname}")
                if server_info:
                    self.log_message.emit(
                        "debug",
                        f"Server {hostname} at {server_info.get('ip', '?')}, "
                        f"version {server_info.get('version', '?')}",
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
        """Dispatch the blocking connection handshake onto the thread pool."""
        self.thread_pool.start(FunctionWorker(self._connect_to_server_impl))

    def _connect_to_server_impl(self):
        # Skip if an attempt is already in flight, so the fast localhost path
        # and a LAN autoconnect cannot both connect.
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
        if not self.selected_server:
            if len(self.discovered_servers) == 0:
                self.log_message.emit("error", "No valid servers available.")
                return
            self.selected_server = self.discovered_servers[0]
            self.log_message.emit(
                "info", f"Connecting to server: {self.selected_server.get('ip')}"
            )

        try:
            with self._control_lock:
                if self.control_socket:
                    with contextlib.suppress(Exception):
                        self.control_socket.close()

                self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.control_socket.settimeout(5.0)
                self.control_socket.connect(
                    (self.selected_server["ip"], self.control_port)
                )

            server_info = self._authenticate_with_server(self.control_socket)

            if not server_info:
                raise AuthenticationError("Authentication with server failed")

            if self.data_socket:
                with contextlib.suppress(Exception):
                    self.data_socket.close()

            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.settimeout(5)
            self.data_socket.connect((self.selected_server["ip"], self.data_port))
            self.data_connected = True

            # Keep the address we actually reached the server on: the payload
            # re-advertises its NIC address, which may be unroutable from here.
            connected_ip = self.selected_server.get("ip")
            self.selected_server.update(server_info)
            if connected_ip:
                self.selected_server["ip"] = connected_ip
            self.data_sources = server_info.get("data_sources", None)

            self.status = ClientStatus.SERVER_CONNECTED
            self.server_connected.emit(True)
        except Exception as e:
            self.status = ClientStatus.SERVER_DISCONNECTED
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

            self.log_message.emit("error", f"Server connection failed: {e}")
            self.server_disconnected.emit(True)

    def disconnect_from_server(self):
        # Stop the receiver before closing the socket it reads.
        self._stop_data_streamer()

        if self.data_saver is not None:
            with contextlib.suppress(Exception):
                self.data_saver.stop_saving()
            self.data_saver = None

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

    ### Configurator commands
    def list_devices(self):
        """Enumerate everything attached, independent of any loaded config."""
        if self.status < ClientStatus.SERVER_CONNECTED:
            self.device_list_failed.emit("Connect to a server first")
            return

        worker = FunctionWorker(self._list_devices_blocking)
        worker.signals.finished.connect(self._on_devices_listed)
        worker.signals.error.connect(self.device_list_failed.emit)
        QThreadPool.globalInstance().start(worker)

    def _list_devices_blocking(self):
        # Enumeration runs every backend's discovery server-side (uhd.find,
        # BIOPAC's WMI walk), which far outlasts the default control timeout.
        response = self._send_command_locked(
            Command.LIST_DEVICES, timeout=DEVICE_OP_COMMAND_TIMEOUT
        )
        resp_type, params = parse_and_validate_response(response)
        if resp_type != Response.DEVICE_LIST.name:
            raise RuntimeError((params or {}).get("message", "Device listing failed"))
        return params or {}

    def _on_devices_listed(self, params):
        devices = params.get("devices", []) or []
        backends = params.get("backends", {}) or {}
        self.log_message.emit("info", f"Found {len(devices)} attached device(s)")
        self.devices_listed.emit(devices, backends)

    def set_device_config(self, device_info: dict, config: dict):
        if self.status < ClientStatus.SERVER_CONNECTED:
            self.device_config_failed.emit("Connect to a server first")
            return

        worker = FunctionWorker(self._set_device_config_blocking, device_info, config)
        worker.signals.finished.connect(
            lambda result: self.device_config_updated.emit(
                result.get("device_info", {}), result.get("message", "")
            )
        )
        worker.signals.error.connect(self.device_config_failed.emit)
        QThreadPool.globalInstance().start(worker)

    def _set_device_config_blocking(self, device_info: dict, config: dict):
        response = self._send_command_locked(
            Command.SET_DEVICE_CONFIG,
            {"device_info": device_info, "config": config},
        )
        resp_type, params = parse_and_validate_response(response)
        if resp_type != Response.DEVICE_CONFIG_UPDATED.name:
            raise RuntimeError(
                (params or {}).get("message", "Could not update the device")
            )
        return params or {}

    ### Device Commands
    def discover_devices(self):
        self.initialize_devices(only_discover=True)

    def initialize_devices(self, only_discover: bool = False):
        if self._discovering_devices:
            self.log_message.emit("warning", "Device discovery already in progress")
            return

        if self.status < ClientStatus.SERVER_CONNECTED:
            self.log_message.emit(
                "warning", "Connect to a server before initializing devices"
            )
            return

        if not self.config.devices:
            self.log_message.emit(
                "warning", "No device configuration loaded to initialize"
            )
            return

        self._discovering_devices = True
        self.group_configs = self.config.to_dict()

        if only_discover:
            cmd = Command.DISCOVER_DEVICES
            self.log_message.emit("debug", "Discovering devices...")
        else:
            cmd = Command.INITIALIZE_DEVICES
            self.log_message.emit("debug", "Initializing devices...")

        worker = DeviceInitWorker(client_ref=self, command=cmd)
        worker.signals.finished.connect(
            lambda status: self._on_device_command_finished(status, only_discover)
        )
        self.thread_pool.start(worker)

    @staticmethod
    def _is_connected(status_value) -> bool:
        """True for DeviceStatus.CONNECTED, as an enum or as its wire value."""
        if status_value == DeviceStatus.CONNECTED:
            return True
        if isinstance(status_value, DeviceStatus):
            return status_value == DeviceStatus.CONNECTED
        return str(status_value) == DeviceStatus.CONNECTED.value

    def _on_device_command_finished(self, group_status_dict, only_discover: bool):
        """Handle the server's {device_id: DeviceStatus} reply.

        Only the groups named in the request appear in it.
        """
        if not group_status_dict or not isinstance(group_status_dict, dict):
            self._discovering_devices = False

            if self.config.devices:
                self.log_message.emit(
                    "warning",
                    "Device command failed: no status returned from server",
                )
                if not only_discover:
                    self.device_init_failed.emit()
            return

        self.device_states = group_status_dict
        self._log_device_outcomes(group_status_dict, only_discover)

        if only_discover:
            self.status = ClientStatus.DEVICES_DISCOVERED
            self.devices_discovered.emit(self.device_states)
        elif any(self._is_connected(v) for v in group_status_dict.values()):
            self.status = ClientStatus.DEVICES_CONNECTED
            self._start_data_streamer_after_init()
            self.device_init_succeeded.emit(self.device_states)
        else:
            failed = ", ".join(self.device_errors or {})
            self.log_message.emit(
                "error",
                "Device initialization failed: no devices connected"
                + (f" (failed: {failed})" if failed else ""),
            )
            self.device_init_failed.emit()

        self._discovering_devices = False

    def _log_device_outcomes(self, group_status_dict, only_discover: bool):
        """Report what happened to each device group, and why when it failed."""
        action = "Discovery" if only_discover else "Initialization"
        good = {DeviceStatus.AVAILABLE.value, DeviceStatus.CONNECTED.value}
        errors = self.device_errors or {}

        for group, state in group_status_dict.items():
            state_text = getattr(state, "value", state)
            if str(state_text) in good:
                self.log_message.emit("info", f"{group}: {state_text}")
                continue

            reason = errors.get(group)
            # The shared catalogue keeps Monitor and Configurator wording identical.
            explained = describe_failure(reason) if reason else ""
            self.log_message.emit(
                "error",
                f"{group}: {state_text}" + (f" -- {explained}" if explained else ""),
            )

        ok = sum(
            1 for v in group_status_dict.values() if str(getattr(v, "value", v)) in good
        )
        self.log_message.emit(
            "debug",
            f"{action} finished: {ok}/{len(group_status_dict)} device group(s) ready",
        )

    def disconnect_device(self):
        self.log_message.emit("info", "Disconnecting devices...")

        if self.status is ClientStatus.STREAMING:
            self.stop_streaming()

        response = self._send_command_locked(command=Command.DISCONNECT_DEVICES)
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.log_message.emit("info", "Devices disconnected")
            self.device_disconnect_succeeded.emit()
        else:
            msg = resp_payload.get("message", "")
            self.log_message.emit("error", f"Disconnect failed: {msg}")

    # Data receiver lifecycle (long-lived for the whole session)
    def _start_data_streamer_after_init(self):
        """Start the data receiver once devices are up and the socket exists."""
        if not self.data_connected or self.data_socket is None:
            return
        self._start_data_streamer()

    def _start_data_streamer(self):
        """(Re)start the data receiver bound to the session data socket."""
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
                self.data_streamer.wait(2000)
            self.data_streamer = None

    # Data streaming handlers
    def start_streaming(self):
        """Dispatch the streaming start onto the thread pool.

        Guarded: a start can take a minute (every worker is an OS process spawn
        on Windows), and while it is in flight the Start button stays live. Each
        extra click used to queue another START_STREAMING behind the control
        lock, so the server was told to start the same session five or six times
        over -- restarting the devices out from under the first attempt.
        """
        with self._start_streaming_lock:
            if self._start_streaming_pending:
                self.log_message.emit(
                    "debug", "Streaming start already in progress; ignoring request"
                )
                return
            self._start_streaming_pending = True
        self.thread_pool.start(FunctionWorker(self._start_streaming_impl))

    def _start_streaming_impl(self):
        try:
            return self._start_streaming_locked()
        finally:
            with self._start_streaming_lock:
                self._start_streaming_pending = False

    def _start_streaming_locked(self):
        if self._discovering_devices:
            self.log_message.emit(
                "warning", "Cannot start streaming while device discovery is in progress"
            )
            return False

        if not self.control_socket:
            self.log_message.emit(
                "error", "Cannot start streaming: not connected to a server"
            )
            return False

        self.log_message.emit("info", "Attempting to start data streaming...")
        self.log_message.emit(
            "debug",
            f"Streaming {len(self.data_sources or [])} data source(s); "
            f"saving {'on' if self.enable_save else 'off'}",
        )
        response = self._send_command_locked(
            command=Command.START_STREAMING,
            params=self.config.to_dict(),
            timeout=STREAMING_COMMAND_TIMEOUT,
        )
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.status = ClientStatus.STREAMING
            self.log_message.emit("info", "Data streaming started")

            # The data socket lives for the whole session; tearing it down on
            # stop/restart leaves the server writing to a dead socket.
            if not self.data_connected:
                self.log_message.emit(
                    "warning",
                    "Data connection not established; reconnect to the server.",
                )
            elif self.data_streamer is None or not self.data_streamer.isRunning():
                self._start_data_streamer()

            self._start_saving()
            self.streaming_started.emit(True)
            self.log_message.emit("debug", "Streaming started successfully")
        else:
            msg = resp_payload.get("message", "") if resp_payload else ""
            self.log_message.emit("error", f"Failed to start streaming: {msg}")

    def _start_saving(self):
        """Create and start the client-side disk writer if saving is enabled."""
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
            device_config = {}
            if self.config is not None:
                device_config = {
                    dev_id: cfg.to_dict() for dev_id, cfg in self.config.devices.items()
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
        # The per-chunk source list is authoritative when present; the list
        # advertised at connect time is the fallback.
        if self.data_saver is not None:
            self.data_saver.add(data)

        chunk_sources = sources if sources else self.data_sources
        self.data_received.emit(data, chunk_sources)

    def stop_streaming(self):
        """Dispatch the streaming stop onto the thread pool."""
        self.thread_pool.start(FunctionWorker(self._stop_streaming_impl))

    def _stop_streaming_impl(self):
        if self._discovering_devices:
            self.log_message.emit(
                "warning", "Cannot stop streaming while device discovery is in progress"
            )
            return False

        self.log_message.emit("debug", "Attempting to stop streaming...")

        response = self._send_command_locked(
            command=Command.STOP_STREAMING, timeout=STREAMING_COMMAND_TIMEOUT
        )
        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.ERROR.name:
            err = resp_payload.get("message", "") if resp_payload else ""
            msg = f"Failed to stop streaming: {err}"
            self.log_message.emit("error", msg)

        # The data socket and receiver stay up for the whole session: the
        # server only pauses its backends, and keeps the same connection.
        if self.data_saver is not None:
            self.data_saver.stop_saving()
            self.data_saver = None

        self.status = ClientStatus.DEVICES_CONNECTED
        self.streaming_stopped.emit(True)
        self.log_message.emit("debug", "Streaming stopped successfully")

    def configure_device(self, device_id, config):
        """Change a running device's parameters through the server."""
        if self._discovering_devices:
            self.log_message.emit(
                "warning",
                "Cannot configure device while device discovery is in progress",
            )
            return False

        self.log_message.emit("info", f"Configuring device: {device_id}")
        response = self._send_command_locked(
            command=Command.UPDATE_RUNNING_PARAMETER,
            params={"id": device_id, "config": config},
        )

        resp_type, resp_payload = parse_and_validate_response(response)

        if resp_type == Response.SUCCESS.name:
            self.log_message.emit("debug", "Successfully updated device parameter")
            # A parameter change can add or remove streams, so republish the
            # source list the server replies with.
            data_sources = (resp_payload or {}).get("data_sources")
            if data_sources is not None:
                self.data_sources = data_sources
                self.data_sources_changed.emit(list(data_sources))
            return True
        else:
            msg = resp_payload.get("message", "") if resp_payload else ""
            self.log_message.emit("debug", f"Failed to update parameter: {msg}")
            return False

    def run_dpic_balance(self, device_id: str):
        if self._discovering_devices:
            self.log_message.emit(
                "warning",
                "Cannot run DPIC balance while device discovery is in progress",
            )
            return False

        self.log_message.emit("info", f"Running DPIC balance for {device_id}")
        response = self._send_command_locked(
            command=Command.RUN_DPIC_BALANCE,
            params={"id": device_id},
        )
        resp_type, resp_payload = parse_and_validate_response(response)
        if resp_type == Response.SUCCESS.name:
            self.log_message.emit("info", "DPIC balance completed")
            return True
        msg = resp_payload.get("message", "DPIC balance failed")
        self.log_message.emit("error", msg)
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

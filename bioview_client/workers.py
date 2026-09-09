import contextlib
import json
import socket
import struct
import time

import numpy as np  # TODO: Investigate if this is strictly needed or not
from bioview_common import (
    DEVICE_OP_COMMAND_TIMEOUT,
    DEVICE_OP_POLL_INTERVAL,
    DISCOVER_TIMEOUT,
    INIT_TIMEOUT_DEFAULT,
    INIT_TIMEOUT_USRP,
    Command,
    Response,
    describe_failure,
    parse_and_validate_response,
    send_command,
)
from bioview_common.datatypes.devices import DeviceType
from PyQt6.QtCore import QObject, QRunnable, QThread, pyqtSignal, pyqtSlot


class FunctionWorkerSignals(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)


class FunctionWorker(QRunnable):
    """Runs a callable on the thread pool so blocking network operations do not
    freeze the UI thread."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = FunctionWorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))


class ScanWorkerSignals(QObject):
    # Emit a server info dict when a BioView server is discovered, or None otherwise
    result = pyqtSignal(object)
    # Emit (level, text) when a host answered but the exchange then failed.
    log_message = pyqtSignal(str, str)


class ScanWorker(QRunnable):
    def __init__(self, ip, control_port, timeout=2):
        super().__init__()
        self.ip = ip
        self.control_port = control_port
        self.timeout = timeout
        self.signals = ScanWorkerSignals()

    def _emit(self, name, *args):
        """Emit a signal by name unless the window went away mid-scan.

        A scan outlives a closed window often enough to matter, and PyQt then
        raises RuntimeError -- from the *attribute access*, not just the emit --
        on a pool thread, where it becomes a bare traceback on a stderr nobody
        is reading.
        """
        with contextlib.suppress(RuntimeError):
            getattr(self.signals, name).emit(*args)

    def run(self):
        """Probe one IP; emit its server info dict, or None.

        Nothing listening is the expected case while sweeping a subnet and is
        silent. A host that *answers* and then fails is a real fault -- a
        protocol mismatch, a truncated frame, a version skew -- and used to be
        indistinguishable from an empty address because one suppress() covered
        the connect and the exchange alike.
        """
        server_info = None
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.ip, self.control_port))
        except OSError:
            # No listener, refused, or unreachable: normal during a scan.
            if s is not None:
                with contextlib.suppress(OSError):
                    s.close()
            self._emit("result", None)
            return

        try:
            response = send_command(sock=s, command=Command.DISCOVER_SERVERS)
            resp_type, resp_payload = parse_and_validate_response(response)
            if resp_type == Response.SUCCESS.name:
                server_info = resp_payload
            else:
                self._emit(
                    "log_message",
                    "warning",
                    f"{self.ip}:{self.control_port} answered discovery with "
                    f"{resp_type}, not SUCCESS",
                )
        except Exception as e:
            self._emit(
                "log_message",
                "warning",
                f"{self.ip}:{self.control_port} accepted a connection but the "
                f"discovery exchange failed: {e}",
            )
        finally:
            with contextlib.suppress(OSError):
                s.close()

        self._emit("result", server_info)


class DeviceInitSignals(QObject):
    # Emit a list of devices when discovery completes
    finished = pyqtSignal(dict)
    # ({group_id: status}, {group_id: reason}) each time the server's view of
    # the groups changes while the command is still running. The server brings
    # the groups up one at a time and has always reported that per group; the
    # client used to keep it to a debug log line, so the status bar sat on
    # "connecting" for every group until the last one finished.
    progress = pyqtSignal(dict, dict)


class DeviceInitWorker(QRunnable):
    """Runs device discovery and initialization off the UI thread."""

    def __init__(self, client_ref, command):
        super().__init__()
        self.client_ref = client_ref
        self.command = command

        if command == Command.DISCOVER_DEVICES:
            self.overall_timeout = DISCOVER_TIMEOUT
        elif command == Command.INITIALIZE_DEVICES:
            has_usrp = any(
                cfg.get_param("device_type") == DeviceType.USRP.value
                for cfg in client_ref.config.devices.values()
            )
            self.overall_timeout = (
                INIT_TIMEOUT_USRP if has_usrp else INIT_TIMEOUT_DEFAULT
            )
        else:
            self.overall_timeout = INIT_TIMEOUT_DEFAULT

        self.signals = DeviceInitSignals()

    def _poll_until_complete(self, deadline: float):
        # The per-command trace skips this poll, so without this a long
        # device init looks like the client has hung.
        started = time.monotonic()
        last_status = None

        while time.monotonic() < deadline:
            # Through the per-command override, which restores the previous
            # value: setting it on the socket left every later command on the
            # 30 s device-operation timeout for the rest of the session.
            response = self.client_ref._send_command_locked(
                command=Command.GET_DEVICE_STATUS,
                timeout=DEVICE_OP_COMMAND_TIMEOUT,
            )
            if not response:
                time.sleep(DEVICE_OP_POLL_INTERVAL)
                continue

            resp_type, resp_payload = parse_and_validate_response(response)
            if not resp_type:
                time.sleep(DEVICE_OP_POLL_INTERVAL)
                continue

            pending = bool((resp_payload or {}).get("pending", False))
            device_status = (resp_payload or {}).get("device_status", {})
            if not pending and device_status:
                return device_status, resp_payload, resp_type

            if device_status != last_status:
                last_status = device_status
                states = ", ".join(f"{k}={v}" for k, v in (device_status or {}).items())
                self.client_ref.log_message.emit(
                    "debug",
                    f"{self.command.name} in progress "
                    f"({time.monotonic() - started:.0f}s)"
                    + (f": {states}" if states else ""),
                )
                errors = (resp_payload or {}).get("device_errors") or {}
                self._emit_progress(device_status, errors)

            time.sleep(DEVICE_OP_POLL_INTERVAL)

        raise TimeoutError(
            f"{self.command.name} timed out after {self.overall_timeout:.0f}s "
            "with the server still reporting the operation as pending"
        )

    def _emit_progress(self, device_status, device_errors):
        """Publish a mid-command status map. Never allowed to break the poll.

        An empty map is dropped: the server clears its group states while it
        re-runs discovery, and a window that acted on that would blank the
        status bar halfway through an initialization.
        """
        if not device_status:
            return
        with contextlib.suppress(RuntimeError):
            self.signals.progress.emit(dict(device_status or {}), dict(device_errors))

    def _extract_result(self, resp_type, resp_payload):
        device_status = (resp_payload or {}).get("device_status", {})
        data_sources = (resp_payload or {}).get("data_sources")
        if data_sources is not None:
            self.client_ref.data_sources = data_sources
        # Why any group failed, so the reason is not stranded server-side.
        self.client_ref.device_errors = (resp_payload or {}).get("device_errors") or {}
        if resp_type == Response.WARNING.name:
            self.client_ref.log_message.emit(
                "warning",
                "Device command completed with server warnings",
            )
        if not device_status:
            raise ValueError("Server returned no device status")
        return device_status

    @pyqtSlot()
    def run(self):
        device_status = {}

        try:
            self.client_ref.control_socket.settimeout(DEVICE_OP_COMMAND_TIMEOUT)

            device_groups = self.client_ref.config.to_dict()
            response = self.client_ref._send_command_locked(
                command=self.command,
                params={"device_groups": device_groups},
            )
            if not response:
                raise ValueError(
                    "No response from server. Connect to a server and try again."
                )

            resp_type, resp_payload = parse_and_validate_response(response)
            if not resp_type:
                raise ValueError("Malformed response from server")

            if resp_type == Response.DEVICE_CONNECTING.name:
                # The acknowledgement already names every group in the request,
                # all of them "connecting". Publishing it puts the whole group
                # list into the status bar before the first one is reached.
                self._emit_progress(
                    (resp_payload or {}).get("device_status", {}),
                    (resp_payload or {}).get("device_errors", {}),
                )
                deadline = time.monotonic() + self.overall_timeout
                _, resp_payload, poll_type = self._poll_until_complete(deadline)
                device_status = self._extract_result(
                    poll_type or Response.SUCCESS.name, resp_payload
                )
            elif resp_type in (Response.SUCCESS.name, Response.WARNING.name):
                device_status = self._extract_result(resp_type, resp_payload)
            elif resp_type == Response.ERROR.name:
                msg = (resp_payload or {}).get("message", "Unknown server error")
                raise ValueError(msg)
            else:
                raise ValueError(f"Unexpected response type: {resp_type}")

            self.signals.finished.emit(device_status)
        except Exception as e:
            self.client_ref.log_message.emit(
                "error", f"Device command failed: {describe_failure(e)}"
            )
            self.signals.finished.emit({})


class DataStreamer(QThread):
    log_message = pyqtSignal(str, str)
    # (data, sources): a (num_sources, num_samples) array plus one source
    # descriptor dict per row.
    data_received = pyqtSignal(np.ndarray, object)

    def __init__(self, data_conn, parent=None):
        super().__init__(parent)
        self.data_conn = data_conn
        self.running = False

    def run(self):
        """Receive real-time data from server"""
        self.running = True
        self.log_message.emit("debug", "Data receiver thread started")

        # A short timeout lets the loop re-check self.running and ride out
        # pauses instead of treating them as a disconnect.
        with contextlib.suppress(Exception):
            self.data_conn.settimeout(1.0)

        while self.running:
            # Receive length-prefixed frame header
            length_data = self._recv_exactly(4)
            if length_data is None:
                break

            data_length = struct.unpack("!I", length_data)[0]

            # Receive the actual data
            data_bytes = self._recv_exactly(data_length)
            if data_bytes is None:
                break

            # Deserialize the data
            data, sources = self._deserialize_data(data_bytes)

            if data is not None:
                # Emit data signal for plotting/saving
                self.data_received.emit(data, sources)

        self.log_message.emit("info", "Data receiving thread stopped")

    def _recv_exactly(self, num_bytes):
        """Receive exactly ``num_bytes``, or None on a real disconnect or stop.

        Transient socket timeouts are tolerated, so the receiver waits out idle
        periods rather than tearing itself down.
        """
        # Collect and join: repeated ``data += chunk`` is quadratic in the
        # number of reads.
        chunks = []
        received = 0
        while received < num_bytes:
            if not self.running:
                return None
            try:
                chunk = self.data_conn.recv(num_bytes - received)
            except socket.timeout:
                continue  # idle gap; keep waiting while still running
            except OSError as e:
                if self.running:
                    self.log_message.emit("error", f"Receiving error: {e}")
                return None
            if not chunk:
                return None  # peer closed the connection
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    def _deserialize_data(self, data_bytes):
        """Deserialize one chunk into ``(data, sources)``."""
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

            sources = header.get("sources")

            return data, sources

        except Exception as e:
            self.log_message.emit("error", f"Data deserialization error: {e}")
            return None, None

    def stop(self):
        self.running = False

import contextlib
import json
import os
import queue
import socket
import struct
import threading
import time
from datetime import datetime

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


class ScanWorker(QRunnable):
    def __init__(self, ip, control_port, timeout=2):
        super().__init__()
        self.ip = ip
        self.control_port = control_port
        self.timeout = timeout
        self.signals = ScanWorkerSignals()

    def run(self):
        # Probe the control port on the target IP and emit a server info dict or None
        server_info = None
        s = None

        with contextlib.suppress(Exception):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.ip, self.control_port))

            # Request discovery and wait for a valid response
            response = send_command(sock=s, command=Command.DISCOVER_SERVERS)

            resp_type, resp_payload = parse_and_validate_response(response)
            if resp_type == Response.SUCCESS.name:
                server_info = resp_payload

        # Close the socket only if it was successfully created
        if s is not None:
            with contextlib.suppress(Exception):
                s.close()

        self.signals.result.emit(server_info)


class DeviceInitSignals(QObject):
    # Emit a list of devices when discovery completes
    finished = pyqtSignal(dict)


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
            self.client_ref.control_socket.settimeout(DEVICE_OP_COMMAND_TIMEOUT)
            response = self.client_ref._send_command_locked(
                command=Command.GET_DEVICE_STATUS,
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

            time.sleep(DEVICE_OP_POLL_INTERVAL)

        raise TimeoutError(
            f"{self.command.name} timed out after {self.overall_timeout:.0f}s "
            "with the server still reporting the operation as pending"
        )

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


# 8-byte magic marking a metadata trailer at the end of a .bvr file
BVR_TRAILER_MAGIC = b"BVRMETA1"


class DataSaver(threading.Thread):
    """Client-side disk writer for .bvr recordings.

    Runs on its own thread so disk I/O never blocks the data receiver. The
    "bioview-raw-v2" file layout is documented in
    bioview-docs/reference/bvr-format.md.
    """

    def __init__(self, save_path, sources=None, device_config=None, log_signal=None):
        super().__init__(daemon=True)
        self.save_path = str(save_path)
        self.sources = sources or []
        self.device_config = device_config or {}
        self._log_signal = log_signal
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._file = None
        self._header_written = False

        # Timestamped device-parameter changes recorded during this run
        self._changes = []
        self._changes_lock = threading.Lock()
        # Timestamped event annotations ("Mark Event") recorded during this run
        self._annotations = []
        self._annotations_lock = threading.Lock()
        self._start_time = None

    def _log(self, level, msg):
        if self._log_signal is not None:
            with contextlib.suppress(Exception):
                self._log_signal.emit(level, msg)

    def start_saving(self):
        try:
            parent = os.path.dirname(self.save_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            # Not a context manager: the file stays open until stop_saving().
            self._file = open(self.save_path, "wb")  # noqa: SIM115
            self._start_time = datetime.now()
            header = {
                "format": "bioview-raw-v2",
                "dtype": "float32",
                "layout": "time_major",
                "num_sources": len(self.sources),
                "sources": self.sources,
                "start_time": self._start_time.isoformat(),
                "start_time_parts": {
                    "year": self._start_time.year,
                    "month": self._start_time.month,
                    "day": self._start_time.day,
                    "hour": self._start_time.hour,
                    "minute": self._start_time.minute,
                    "second": self._start_time.second,
                },
                "device_config": self.device_config,
            }
            header_bytes = json.dumps(header, default=str).encode("utf-8")
            self._file.write(struct.pack("!I", len(header_bytes)) + header_bytes)
            self._header_written = True
            self.start()
            self._log("info", f"Saving data to {self.save_path}")
        except Exception as e:
            self._log("error", f"Unable to open save file: {e}")
            self._file = None

    def add(self, data):
        if self._file is not None and not self._stop_event.is_set():
            self._queue.put(data)

    def record_change(self, device_id: str, param: str, value):
        """Append a timestamped device-parameter change to the recording's
        metadata trailer."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "device_id": device_id,
            "param": param,
            "value": value,
        }
        with self._changes_lock:
            self._changes.append(entry)

    def record_annotation(self, text: str) -> dict:
        """Append a timestamped annotation to the recording trailer."""
        now = datetime.now()
        elapsed = None
        if self._start_time is not None:
            elapsed = (now - self._start_time).total_seconds()
        entry = {
            "timestamp": now.isoformat(),
            "elapsed_seconds": elapsed,
            "text": str(text),
        }
        with self._annotations_lock:
            self._annotations.append(entry)
        return entry

    def _write_trailer(self):
        if self._file is None:
            return
        with self._changes_lock:
            changes = list(self._changes)
        with self._annotations_lock:
            annotations = list(self._annotations)
        trailer = {
            "end_time": datetime.now().isoformat(),
            "param_changes": changes,
            "Annotations": annotations,
        }
        trailer_bytes = json.dumps(trailer, default=str).encode("utf-8")
        self._file.write(trailer_bytes)
        self._file.write(struct.pack("!Q", len(trailer_bytes)))
        self._file.write(BVR_TRAILER_MAGIC)

    def run(self):
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                data = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                # data is (num_sources, num_samples); store time-major for easy append
                block = np.ascontiguousarray(np.asarray(data).T, dtype=np.float32)
                self._file.write(block.tobytes())
            except Exception as e:
                self._log("error", f"Save write error: {e}")

        with contextlib.suppress(Exception):
            if self._file is not None:
                self._write_trailer()
                self._file.flush()
                self._file.close()

    def stop_saving(self):
        self._stop_event.set()

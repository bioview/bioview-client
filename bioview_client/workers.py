import contextlib
import json
import os
import queue
import socket
import struct
import threading
from datetime import datetime

import numpy as np  # TODO: Investigate if this is strictly needed or not
from bioview_common import (
    ClientStatus,
    Command,
    DataSource,
    Response,
    parse_and_validate_response,
    send_command,
)
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
    """
    Handler to deal with device discovery and initialization in the background.
    We do this to prevent UI from getting blocked
    """

    def __init__(self, client_ref, command):
        super().__init__()
        self.client_ref = client_ref
        self.command = command

        if command == Command.DISCOVER_DEVICES:
            self.timeout = 60
        elif command == Command.INITIALIZE_DEVICES:
            self.timeout = 120
        else:
            self.timeout = 15

        self.signals = DeviceInitSignals()

    @pyqtSlot()
    def run(self):
        device_status = {}

        try:
            # Device discovery can be slow on server side; use a longer timeout
            self.client_ref.control_socket.settimeout(self.timeout)

            response = self.client_ref._send_command_locked(
                command=self.command,
                params={
                    "device_groups": self.client_ref.group_configs,
                },
            )

            resp_type, resp_payload = parse_and_validate_response(response)

            if resp_type == Response.SUCCESS.name:
                device_status = resp_payload.get("device_status", {})
                # Capture the data sources advertised by the server so the UI can
                # populate the plot-source selector after initialization.
                data_sources = resp_payload.get("data_sources")
                if data_sources is not None:
                    self.client_ref.data_sources = data_sources
            elif resp_type == Response.ERROR.name:
                msg = resp_payload.get("message", "") if resp_payload else ""
                raise ValueError(f"Invalid device format received: {msg}")

            self.signals.finished.emit(device_status)
        except Exception as e:
            # On error, emit empty dict
            self.client_ref.log_message.emit("error", f"Device discovery failed: {e}")
            self.signals.finished.emit({})


class DataStreamer(QThread):
    log_message = pyqtSignal(str, str)
    # Emits (data, sources) where data is (num_sources, num_samples) and sources
    # is the ordered list of source descriptor dicts describing each row.
    data_received = pyqtSignal(np.ndarray, object)

    def __init__(self, data_conn, parent=None):
        super().__init__(parent)
        self.data_conn = data_conn
        self.running = False

    def run(self):
        """Receive real-time data from server"""
        self.running = True
        self.log_message.emit("debug", "Data receiving thread started")

        # Use a short socket timeout so the loop can periodically re-check
        # self.running and ride out idle periods (e.g. while streaming is paused
        # between Stop and Start) instead of treating them as a disconnect. This
        # lets a single receiver live for the whole session.
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
        """Receive exactly num_bytes from the data socket. Returns None only on a
        real disconnect or when the worker is stopped; transient socket timeouts
        are tolerated so the receiver keeps waiting while idle."""
        data = b""
        while len(data) < num_bytes:
            if not self.running:
                return None
            try:
                chunk = self.data_conn.recv(num_bytes - len(data))
            except socket.timeout:
                continue  # idle gap; keep waiting while still running
            except OSError as e:
                if self.running:
                    self.log_message.emit("error", f"Receiving error: {e}")
                return None
            if not chunk:
                return None  # peer closed the connection
            data += chunk
        return data

    def _deserialize_data(self, data_bytes):
        """Deserialize a numpy data chunk and its source metadata from the server.

        Returns a (data, sources) tuple where sources is the ordered list of
        source descriptor dicts (or None if absent)."""
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
    """Client-side disk writer. Runs on its own thread and appends full-rate
    chunks to a self-describing binary file so disk I/O never blocks the data
    receiving thread.

    File format ("bioview-raw-v2"):
        [Header Length (4 bytes, big-endian)][JSON header]
        [float32 samples, time-major: each chunk stored as (num_samples, num_sources)]
        [JSON trailer]
        [Trailer Length (8 bytes, big-endian)]
        [8-byte magic "BVRMETA1"]

    The JSON header records dtype, layout, the ordered source descriptors, the
    recording start time, and a snapshot of device configuration constants. The
    trailer (written when the recording closes) records the end time and any
    timestamped device-parameter changes made while recording. A reader locates
    the trailer via the magic + length at EOF; the sample region is everything
    between the header and the trailer."""

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
            self._file = open(self.save_path, "wb")
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

    def _write_trailer(self):
        if self._file is None:
            return
        with self._changes_lock:
            changes = list(self._changes)
        trailer = {
            "end_time": datetime.now().isoformat(),
            "param_changes": changes,
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

import contextlib
import json
import socket
import struct

import numpy as np  # TODO: Investigate if this is strictly needed or not
from bioview_common import ClientStatus, Command, DataSource, Response
from PyQt6.QtCore import QObject, QRunnable, QThread, pyqtSignal, pyqtSlot

from bioview_client.utils import (
    parse_and_validate_response,
    send_command,
)


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

        with contextlib.suppress(Exception):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.ip, self.control_port))

            # Request discovery and wait for a valid response
            response = send_command(sock=s, command=Command.DISCOVER_SERVERS)

            resp_type, resp_payload = parse_and_validate_response(response)
            if resp_type == Response.SUCCESS.name:
                server_info = resp_payload

        # Finally, emit
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

            response = send_command(
                sock=self.client_ref.control_socket,
                command=self.command,
                params={
                    "device_groups": self.client_ref.group_configs,
                },
            )

            resp_type, resp_payload = parse_and_validate_response(response)

            if resp_type == Response.SUCCESS.name:
                device_status = resp_payload.get("device_status", {})
            elif resp_type == Response.ERROR.name:
                msg = resp_payload.get("message", "")
                raise ValueError(f"Invalid device format received: {msg}")

            self.signals.finished.emit(device_status)
        except Exception as e:
            # On error, emit empty dict
            self.client_ref.log_message.emit("error", f"Device discovery failed: {e}")
            self.signals.finished.emit({})


class DataStreamer(QThread):
    log_message = pyqtSignal(str, str)
    data_received = pyqtSignal(DataSource, np.ndarray)

    def __init__(self, data_conn, parent=None):
        super().__init__(parent)
        self.data_conn = data_conn
        self.running = False

    def run(self):
        """Receive real-time data from server"""
        self.running = True
        self.log_message.emit("debug", "Data receiving thread started")

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
                chunk = self.self.data_conn.recv(num_bytes - len(data))
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

import contextlib
import socket

from bioview_common import Command, Response
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

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

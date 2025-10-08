import json
import struct

import numpy as np
from bioview_common import Command, Response

from bioview_client.handler import Client, DataStreamer
from bioview_client.utils.network import parse_and_validate_response


def test_parse_and_validate_response_ok():
    msg = json.dumps({"type": Response.INFO.value, "payload": {"a": 1}})
    t, p = parse_and_validate_response(msg, Response.INFO.value)
    assert t == Response.INFO.value
    assert p.get("a") == 1


def test_data_streamer_deserialize():
    # Create a small numpy array, serialize to our wire format
    arr = np.arange(6, dtype=np.float32).reshape((2, 3))
    header = {"shape": list(arr.shape), "dtype": str(arr.dtype)}
    header_bytes = json.dumps(header).encode("utf-8")
    header_len = struct.pack("!I", len(header_bytes))
    payload = header_len + header_bytes + arr.tobytes()

    ds = DataStreamer(running=True)
    # Use the internal deserializer directly
    result = ds._deserialize_data(payload)
    assert isinstance(result, np.ndarray)
    assert result.shape == arr.shape
    assert result.dtype == arr.dtype


class FakeSocket:
    def __init__(self, response_dict):
        self.response = json.dumps(response_dict).encode("utf-8")
        self.sent = b""

    def settimeout(self, t):
        pass

    def send(self, data):
        self.sent += data

    def recv(self, bufsize):
        return self.response

    def close(self):
        pass


def test_send_control_command_monkey(monkeypatch):
    # create client and set control_socket to fake socket
    client = Client()
    # fake successful response
    resp = {"type": Response.SUCCESS.value, "payload": {}}
    fake = FakeSocket(resp)
    client.control_socket = fake
    client.control_connected = True

    res = client.send_control_command(Command.PING_SERVER)
    assert isinstance(res, dict)
    assert res.get("type") == Response.SUCCESS.value

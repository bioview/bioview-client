import json
import socket

from bioview_common import CONTROL_PORT

from bioview_client.utils.discovery import discover_via_broadcast


class FakeUDPSocket:
    def __init__(self, *args, **kwargs):
        self._timeout = None
        self._sent = []
        self._returned = False

    def setsockopt(self, *args, **kwargs):
        pass

    def settimeout(self, t):
        self._timeout = t

    def sendto(self, msg, addr):
        # record message but do nothing
        self._sent.append((msg, addr))

    def recvfrom(self, bufsize):
        # return one fake response, then simulate timeout via raising socket.timeout
        if not self._returned:
            self._returned = True
            payload = {
                "type": "DISCOVER_RESPONSE",
                "payload": {"server_info": {"hostname": "test-srv"}},
            }
            return (json.dumps(payload).encode("utf-8"), ("192.0.2.5", CONTROL_PORT))
        raise socket.timeout()

    def recv(self, bufsize):
        raise NotImplementedError()

    def close(self):
        pass


def test_discover_via_broadcast_monkeypatch(monkeypatch):
    # Replace socket.socket with our fake UDP socket factory
    def fake_socket(family=0, type=0, proto=0):
        return FakeUDPSocket()

    monkeypatch.setattr(socket, "socket", fake_socket)

    results = discover_via_broadcast(timeout=0.1, targets=["192.0.2.5"])
    assert isinstance(results, list)
    assert len(results) == 1
    r = results[0]
    assert r.get("hostname") == "test-srv"
    assert r.get("address") == "192.0.2.5"

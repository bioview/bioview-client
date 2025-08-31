import json
import socket
import threading
import time

from bioview_client.utils.discovery import discover_via_broadcast


def _start_temp_responder(port, hostname="testhost"):
    def responder():
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("127.0.0.1", port))
        for _ in range(3):
            data, addr = udp.recvfrom(4096)
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if msg.get("type") == "DISCOVER_REQUEST":
                resp = {
                    "type": "INFO",
                    "payload": {"server_info": {"hostname": hostname}},
                }
                udp.sendto(json.dumps(resp).encode("utf-8"), addr)
        udp.close()

    t = threading.Thread(target=responder, daemon=True)
    t.start()
    time.sleep(0.05)
    return t


def test_discovery_unicast():
    port = 9999
    _start_temp_responder(port)
    servers = discover_via_broadcast(timeout=1.0, port=port, targets=["127.0.0.1"])
    assert isinstance(servers, list)
    assert len(servers) >= 1
    assert servers[0].get("hostname") == "testhost"

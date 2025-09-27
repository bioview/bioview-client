import contextlib
import hashlib
import json
import socket
import threading
import time

import pytest
from bioview_common import APP_VERSION, Command, Response

# Import server from the sibling repo path; tests assume the server package is available on PYTHONPATH
from bioview_server.server import Server


def compute_token(challenge: str, token: str) -> str:
    m = hashlib.sha256()
    m.update(f"{challenge}:{token}".encode())
    return m.hexdigest()


@pytest.fixture(scope="module")
def server_instance():
    # start server on ephemeral ports to avoid conflict
    srv = Server(control_port=0, data_port=0, discoverable=False)
    t = threading.Thread(target=srv.start, daemon=True)
    t.start()
    # wait for the server to bind
    time.sleep(0.5)
    yield srv

    with contextlib.suppress(Exception):
        srv.stop()


def handshake(host, port, token_override=None):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    s.connect((host, port))
    syn = {
        "type": Command.CONNECT_SERVER.name,
        "payload": {
            "hostname": "pytest",
            "app_name": "pytest",
            "app_version": "0.0.0",
            "timestamp": time.time(),
        },
    }
    s.send(json.dumps(syn).encode("utf-8"))
    challenge_raw = s.recv(8192).decode("utf-8")
    challenge_msg = json.loads(challenge_raw)
    challenge = challenge_msg["payload"]["challenge"]
    if token_override is None:
        token = compute_token(challenge, APP_VERSION)
    else:
        token = token_override
    auth = {"type": Command.AUTHENTICATE_CLIENT.name, "payload": {"token": token}}
    s.send(json.dumps(auth).encode("utf-8"))
    final = s.recv(8192).decode("utf-8")
    s.close()
    return json.loads(final)


def test_auth_success_and_failure(server_instance):
    srv = server_instance
    host = "127.0.0.1"
    port = getattr(srv, "control_port", 0)
    # If the server was started with port 0 (ephemeral), try to discover the real bound port
    if not port:
        port = None
        for name in dir(srv):
            if "control" in name:
                try:
                    obj = getattr(srv, name)
                except Exception:
                    continue
                # look for socket-like objects
                if hasattr(obj, "getsockname"):
                    try:
                        sockname = obj.getsockname()
                        if isinstance(sockname, tuple) and len(sockname) >= 2:
                            port = sockname[1]
                            break
                    except Exception:
                        continue
        if not port:
            pytest.skip("Could not determine server control port")

    # wrong token -> authentication_failure
    res = handshake(host, port, token_override="wrong")
    assert res.get("type") == Response.AUTHENTICATION_FAILURE.value

    # correct token -> authentication_success
    res = handshake(host, port)
    assert res.get("type") == Response.AUTHENTICATION_SUCCESS.value
    # server_info should be present and serializable
    assert isinstance(res.get("payload", {}).get("server_info"), dict)


if __name__ == "__main__":
    # Allow running this file directly for quick diagnostics (fallback if pytest collection fails)
    srv = Server(control_port=0, data_port=0, discoverable=False)
    t = threading.Thread(target=srv.start, daemon=True)
    t.start()
    time.sleep(0.5)
    host = "127.0.0.1"
    port = srv.control_port
    print("--- Handshake: wrong token ---")
    print(json.dumps(handshake(host, port, token_override="wrong"), indent=2))
    print("--- Handshake: correct token ---")
    print(json.dumps(handshake(host, port), indent=2))

    with contextlib.suppress(Exception):
        srv.stop()

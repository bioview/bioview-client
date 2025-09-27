import contextlib
import hashlib
import json
import socket
import threading
import time

import pytest
from bioview_common import APP_VERSION, Command, Response
from bioview_server.server import Server


def compute_token(challenge: str, token: str) -> str:
    m = hashlib.sha256()
    m.update(f"{challenge}:{token}".encode())
    return m.hexdigest()


@pytest.fixture(scope="module")
def server_instance():
    srv = Server(control_port=0, data_port=0, discoverable=False)
    t = threading.Thread(target=srv.start, daemon=True)
    t.start()
    time.sleep(0.5)
    yield srv

    with contextlib.suppress(Exception):
        srv.stop()


def resolve_control_port(srv):
    port = getattr(srv, "control_port", 0)
    if port:
        return "127.0.0.1", port
    # inspect for socket-like attributes
    for name in dir(srv):
        if "control" in name:
            try:
                obj = getattr(srv, name)
            except Exception:
                continue
            if hasattr(obj, "getsockname"):
                try:
                    sockname = obj.getsockname()
                    if isinstance(sockname, tuple) and len(sockname) >= 2:
                        return "127.0.0.1", sockname[1]
                except Exception:
                    continue
    pytest.skip("Could not determine server control port")


def handshake_and_get_server_info(host, port):
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
    token = compute_token(challenge, APP_VERSION)
    auth = {"type": Command.AUTHENTICATE_CLIENT.name, "payload": {"token": token}}
    s.send(json.dumps(auth).encode("utf-8"))
    final = s.recv(8192).decode("utf-8")
    s.close()
    return json.loads(final)


def test_server_info_is_json_serializable(server_instance):
    srv = server_instance
    host, port = resolve_control_port(srv)
    res = handshake_and_get_server_info(host, port)
    assert res.get("type") == Response.AUTHENTICATION_SUCCESS.value
    server_info = res.get("payload", {}).get("server_info")
    assert isinstance(server_info, dict)
    # Ensure json serialization doesn't raise
    json.dumps(server_info)


def test_missing_token_payload_results_in_auth_failure(server_instance):
    srv = server_instance
    host, port = resolve_control_port(srv)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    s.connect((host, port))
    syn = {
        "type": Command.CONNECT_SERVER.value,
        "payload": {
            "hostname": "pytest",
            "app_name": "pytest",
            "app_version": "0.0.0",
            "timestamp": time.time(),
        },
    }
    s.send(json.dumps(syn).encode("utf-8"))
    # receive challenge but then send malformed auth payload (missing 'token')
    _ = s.recv(8192)
    bad_auth = {"type": Command.AUTHENTICATE_CLIENT.value, "payload": {}}
    s.send(json.dumps(bad_auth).encode("utf-8"))
    final = s.recv(8192).decode("utf-8")
    s.close()
    obj = json.loads(final)
    # server should not accept this; expect authentication failure or error
    assert obj.get("type") in (
        Response.AUTHENTICATION_FAILURE.value,
        Response.ERROR.value,
    )

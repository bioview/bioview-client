import contextlib
import json
import socket
import time
from typing import Dict, List, Optional

from bioview_common import CONTROL_PORT


try:
    from bioview_client.utils.zeroconf_discovery import discover_via_mdns
except Exception:
    discover_via_mdns = None


def _build_send_addrs(targets_list: Optional[List[str]], port_num: int) -> List[tuple]:
    if targets_list:
        return [(t, port_num) for t in targets_list]
    return [("<broadcast>", port_num)]


def _parse_and_append_response(
    data_bytes: bytes, addr_tuple, collected: List[Dict]
) -> bool:
    try:
        resp = json.loads(data_bytes.decode("utf-8"))
        payload = resp.get("payload", {}) if isinstance(resp, dict) else {}
        server_info = payload.get("server_info") if isinstance(payload, dict) else None
        if not server_info:
            server_info = {"hostname": addr_tuple[0]}
        server_info.setdefault("address", addr_tuple[0])
        if not any(s.get("address") == server_info.get("address") for s in collected):
            collected.append(server_info)
            return True
    except Exception:
        return False
    return False


def _send_requests(sock: socket.socket, msg: bytes, addrs: List[tuple]) -> None:
    for addr in addrs:
        with contextlib.suppress(Exception):
            sock.sendto(msg, addr)


def _collect_responses(sock: socket.socket, timeout: float) -> List[Dict]:
    server_infos: List[Dict] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            break
        except Exception:
            break

        _parse_and_append_response(data, addr, server_infos)

    return server_infos


def discover_via_broadcast(
    timeout: float = 1.0,
    port: int = CONTROL_PORT,
    targets: Optional[List[str]] = None,
) -> List[Dict]:
    """Discover BioView servers via zeroconf or UDP broadcast.

    Returns a list of server_info dicts with at least `hostname` and `address`.
    """
    # Try zeroconf/mdns first if available
    if discover_via_mdns:
        try:
            mdns_results = discover_via_mdns(timeout=timeout)
            if mdns_results:
                return mdns_results
        except Exception:
            pass

    msg = json.dumps({"type": "DISCOVER_REQUEST", "payload": {}}).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    send_addrs = _build_send_addrs(targets, port)

    try:
        if not targets:
            # best-effort to enable broadcast when used
            with contextlib.suppress(Exception):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        _send_requests(sock, msg, send_addrs)
        server_infos = _collect_responses(sock, timeout)
    finally:
        sock.close()

    return server_infos

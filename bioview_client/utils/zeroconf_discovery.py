import socket
from typing import Dict, List, Optional

from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf


SERVICE_TYPE = "_bioview._tcp.local."


class _Collector:
    def __init__(self):
        self.results = []

    def remove_service(self, zeroconf, type, name):
        pass

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            props = {}
            try:
                for k, v in info.properties.items():
                    try:
                        props[k.decode()] = v.decode()
                    except Exception:
                        props[k] = v
            except Exception:
                props = {}

            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
            server_info = {
                "hostname": info.server,
                "address": addresses[0] if addresses else None,
            }
            server_info.update(props)
            self.results.append(server_info)


def discover_via_mdns(timeout: float = 1.0) -> List[Dict]:
    zc = Zeroconf()
    collector = _Collector()
    ServiceBrowser(zc, SERVICE_TYPE, collector)
    try:
        import time

        time.sleep(timeout)
        return collector.results
    finally:
        zc.close()


def register_service(
    name: str, port: int, properties: Optional[dict] = None
) -> ServiceInfo:
    zc = Zeroconf()
    props = {}
    if properties:
        for k, v in properties.items():
            if isinstance(v, str):
                props[k] = v.encode("utf-8")
            else:
                props[k] = v

    hostname = socket.gethostname() + ".local."
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(socket.gethostbyname(socket.gethostname()))],
        port=port,
        properties=props,
        server=hostname,
    )
    zc.register_service(info)
    return info

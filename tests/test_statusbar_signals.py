import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bioview_client.components.status_bar import ServerConnector, StatusBar


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_statusbar_forwards_signals(qapp):
    sb = StatusBar(parent=None)

    received = {}

    def on_scan():
        received["scan"] = True

    def on_connect(server):
        received["connect"] = server

    # connect to forwarded signals
    sb.network_scan_requested.connect(on_scan)
    sb.server_connection_requested.connect(on_connect)

    # Trigger server connector scan
    sc: ServerConnector = sb.server_connector
    sc.scan_network()
    assert received.get("scan") is True

    # Simulate a discovered server and selection
    sc.discovered_servers = [{"hostname": "h", "address": "1.2.3.4"}]
    sc.selected_server = sc.discovered_servers[0]
    sc.connect_to_server()
    assert received.get("connect") == {"hostname": "h", "address": "1.2.3.4"}

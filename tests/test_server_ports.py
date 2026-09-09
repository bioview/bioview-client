"""A window talks to its server on the ports that server is actually using.

The launcher accepts --control-port/--data-port, but they used to stop there:
the window fell back to the compiled-in defaults, so the one escape hatch for a
machine where 8998 is already taken quietly did nothing. The control port is
handed down from the launcher (it is needed to find the server at all) and the
data port is advertised by the server itself, which is the only thing that
knows where it is listening.
"""
import pytest
from bioview_common import CONTROL_PORT, DATA_PORT
from bioview_server.server import Server

from bioview_client.handler import Client


@pytest.fixture
def client(qapp):
    return Client(control_port=9101, data_port=9102)


def test_the_data_port_comes_from_the_server_that_was_found(client):
    client.selected_server = {"ip": "127.0.0.1", "data_port": 9202}
    assert client._server_data_port() == 9202


def test_a_server_that_advertises_nothing_falls_back_to_the_configured_port(client):
    """An older server says nothing about its ports; assume the usual ones."""
    client.selected_server = {"ip": "127.0.0.1"}
    assert client._server_data_port() == 9102


def test_a_nonsense_advertisement_falls_back_rather_than_raising(client):
    client.selected_server = {"ip": "127.0.0.1", "data_port": "not a port"}
    assert client._server_data_port() == 9102


def test_a_server_advertises_the_ports_it_is_listening_on():
    srv = Server(local_only=True, control_port=9301, data_port=9302)
    assert srv.info["control_port"] == 9301
    assert srv.info["data_port"] == 9302


def test_the_defaults_still_line_up_when_nobody_asks_for_anything(qapp):
    """The overwhelmingly common case: no flags, no advertisement, no surprise."""
    plain = Client()
    plain.selected_server = {"ip": "127.0.0.1"}
    assert plain.control_port == CONTROL_PORT
    assert plain._server_data_port() == DATA_PORT

"""A window notices when its server goes away.

The client only ever found out that a server had gone when it next sent a
command -- and nothing then took it out of the connected state. The window sat
there believing it was connected while every action failed, and localhost
autoconnect (which stops once connected) never tried again. The handler thread
now watches the control socket for a closed peer.
"""
import contextlib
import socket
import threading
import time

import pytest
from bioview_common import ClientStatus

from bioview_client.handler import Client


@pytest.fixture
def client(qapp):
    return Client()


@pytest.fixture
def socket_pair():
    """A connected pair of real sockets, so select()/recv() behave for real."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    near = socket.socket()
    accepted = {}

    def _accept():
        accepted["sock"], _ = listener.accept()

    thread = threading.Thread(target=_accept)
    thread.start()
    near.connect(listener.getsockname())
    thread.join(timeout=5)
    listener.close()

    far = accepted["sock"]
    yield near, far

    for sock in (near, far):
        with contextlib.suppress(OSError):
            sock.close()


def test_an_open_connection_is_not_reported_as_dropped(client, socket_pair):
    near, _far = socket_pair
    client.control_socket = near
    assert client._connection_dropped() is False


def test_a_closed_peer_is_reported_as_dropped(client, socket_pair):
    near, far = socket_pair
    client.control_socket = near
    far.close()
    assert client._connection_dropped() is True


def test_a_pending_reply_is_not_mistaken_for_a_hangup(client, socket_pair):
    """Readable does not mean closed; only a zero-length peek does."""
    near, far = socket_pair
    client.control_socket = near
    far.sendall(b"a reply")
    assert client._connection_dropped() is False
    # ...and the peek must not have consumed it.
    assert near.recv(7) == b"a reply"


def test_no_socket_at_all_counts_as_dropped(client):
    client.control_socket = None
    assert client._connection_dropped() is True


def test_a_socket_busy_with_another_command_is_left_alone(client, socket_pair):
    """The control lock is taken without blocking, so this never peeks at a reply
    another thread is in the middle of reading."""
    near, far = socket_pair
    client.control_socket = near
    far.close()

    client._control_lock.acquire()
    try:
        assert client._connection_dropped() is False
    finally:
        client._control_lock.release()


def test_a_dropped_connection_takes_the_client_out_of_the_connected_state(
    client, socket_pair, monkeypatch
):
    near, far = socket_pair
    client.control_socket = near
    client.status = ClientStatus.SERVER_CONNECTED
    far.close()

    disconnected = []
    client.server_disconnected.connect(disconnected.append)

    # Run exactly one pass of the handler loop: the sleep at the end of each
    # iteration is where we stop it.
    client.running = True
    monkeypatch.setattr(time, "sleep", lambda _s: setattr(client, "running", False))
    client.run()

    assert client.status == ClientStatus.SERVER_DISCONNECTED
    assert disconnected, "the UI has to be told, or it cannot re-latch"

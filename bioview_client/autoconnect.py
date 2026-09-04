"""Latching onto a localhost server, shared by the Monitor and Configurator."""
from bioview_common import ClientStatus
from PyQt6.QtCore import QTimer


# How often to re-probe while unconnected.
DEFAULT_INTERVAL_MS = 1000


def start_localhost_autoconnect(
    client, parent=None, interval_ms=DEFAULT_INTERVAL_MS, keep_latched=False
):
    """Probe localhost until ``client`` is connected, then stop.

    ``keep_latched`` idles the timer instead of stopping it, so the window
    reconnects by itself; only for windows with no server picker of their own.
    Returns the QTimer, or None when there is no client.
    """
    if client is None:
        return None

    timer = QTimer(parent)

    def _try_localhost():
        if client.status >= ClientStatus.SERVER_CONNECTED:
            if not keep_latched:
                timer.stop()
            return
        client.quick_connect_localhost()

    timer.timeout.connect(_try_localhost)
    timer.start(interval_ms)

    # Probe immediately too: the server is usually already up.
    _try_localhost()

    return timer

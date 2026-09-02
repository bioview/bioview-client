"""Latching onto a localhost server, shared by the Monitor and the Configurator.

Both windows want the same behaviour -- probe 127.0.0.1 cheaply and connect the
moment a local server answers, so neither needs a manual "discover servers"
step and either can be launched before the server and still catch it when it
appears. The two had grown their own copies of this timer.
"""
from bioview_common import ClientStatus
from PyQt6.QtCore import QTimer


#: How often to re-probe while unconnected. The probe is cheap and runs off the
#: UI thread, so a second is frequent enough to feel immediate without being
#: enough traffic to matter.
DEFAULT_INTERVAL_MS = 1000


def start_localhost_autoconnect(
    client, parent=None, interval_ms=DEFAULT_INTERVAL_MS, keep_latched=False
):
    """Probe localhost until ``client`` is connected, then stop.

    With ``keep_latched`` the timer is not stopped on success, only idled: it
    resumes probing if the connection is ever lost, so a window whose server
    goes away and comes back reconnects on its own instead of staying dead. That
    is only wanted where localhost is the *only* server the window can use (the
    Configurator); a window with a server picker would otherwise drag the user
    back to localhost every time they disconnected on purpose.

    Returns the QTimer so the caller can keep it alive and stop it early; the
    timer is parented to ``parent`` when one is given. Returns None if there is
    no client to connect.
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

    # Probe immediately as well: waiting a whole interval to make the first
    # attempt is a visible delay on the common path where the server is
    # already up.
    _try_localhost()

    return timer

from enum import Enum
from .theme import get_qcolor


# Handle connection as an enum for better clarity
class ConnectionStatus(Enum):
    DISCONNECTED = ("Not Connected", get_qcolor("red"))
    CONNECTING = ("Connecting", get_qcolor("yellow"))
    CONNECTED = ("Connected", get_qcolor("green"))


class RunningStatus(Enum):
    NOINIT = False
    RUNNING = True
    STOPPED = False
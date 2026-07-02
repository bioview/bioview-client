"""Branding asset helpers.

Resolves the packaged BioView icon so both GUIs (Monitor + Configurator) and the
Qt application share one consistent app icon in the window title bar, the
dock/taskbar, and app launchers. A rasterized PNG is preferred for maximum
portability across frozen builds; the source SVG is used as a fallback.
"""
from pathlib import Path

# The application id used for Linux desktop integration (Wayland/KDE match the
# running window to its .desktop entry -- and thus its launcher icon -- by this).
APP_DESKTOP_NAME = "org.bioview.BioView"

_ASSETS_DIR = Path(__file__).resolve().parent


def get_app_icon_path() -> str:
    """Absolute path to the application icon (PNG preferred, SVG fallback)."""
    png = _ASSETS_DIR / "icon.png"
    if png.exists():
        return str(png)
    return str(_ASSETS_DIR / "favicon.svg")


def get_app_icon():
    """A ``QIcon`` for the application icon.

    Imported lazily so this module stays importable without a Qt runtime (useful
    for headless tests that only need the asset path)."""
    from PyQt6.QtGui import QIcon

    return QIcon(get_app_icon_path())

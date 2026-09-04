"""Branding assets. The rasterized PNG is preferred; the SVG is a fallback."""
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
    """A ``QIcon`` for the application icon. Qt is imported lazily."""
    from PyQt6.QtGui import QIcon

    return QIcon(get_app_icon_path())

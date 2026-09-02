"""Shared setup for the BioView client tests.

The client is a PyQt6 app; we force Qt's offscreen platform so tests run
headless (CI / no display) and provide a single QApplication for the session so
QObject-derived classes (Client, widgets) can be constructed."""
import os


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Imported after the environment is set: Qt picks its platform plugin at import
# time, and pytest pulls in the Qt-dependent fixtures below.
import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_window_registry(tmp_path_factory, monkeypatch):
    """Keep the launcher's window registry out of the user's real ~/.bioview.

    Every test that touches _ensure_server / _release_server writes to it, and a
    test run must not add itself to the list of live BioView windows on the
    developer's machine.
    """
    from bioview_client import launch

    # Deliberately not tmp_path: tests that check exactly which files a
    # recording produced assert on that directory's contents.
    registry = tmp_path_factory.mktemp("bioview_cache") / "windows.json"
    registry.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(launch, "_registry_path", lambda: registry)
    yield registry

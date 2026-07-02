"""Shared setup for the BioView client tests.

The client is a PyQt6 app; we force Qt's offscreen platform so tests run
headless (CI / no display) and provide a single QApplication for the session so
QObject-derived classes (Client, widgets) can be constructed."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app

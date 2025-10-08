import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bioview_client.components.log_display import LogDisplayPanel
from bioview_client.components.plot_grid import PlotGrid
from bioview_client.components.status_bar import (
    DeviceStatusPanel,
    ServerConnector,
    StatusBar,
)
from bioview_client.components.text_dialog import TextDialog
from bioview_client.constants.configuration import Configuration


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_server_connector_basic(qapp):
    sc = ServerConnector(parent=None)
    # simulate scan complete
    sc.on_scan_complete([{"hostname": "srv1", "address": "127.0.0.1"}])
    assert sc.server_dropdown.count() == 1


def test_status_bar_and_device_panel(qapp):
    StatusBar(parent=None)
    # Use DeviceStatusPanel directly
    dsp = DeviceStatusPanel({"dev1": {"state": None}})
    dsp.add_device("dev2")
    dsp.update_device_state("dev2", None)


def test_log_display_and_text_dialog(qapp):
    import logging

    logger = logging.getLogger("test_logger")
    ld = LogDisplayPanel(logger)
    ld.log_message("info", "hello world")

    td = TextDialog()
    td.update_instruction_text("hi")
    td.toggle_ui(True)
    td.toggle_ui(False)


def test_plot_grid_basic(qapp):
    cfg = Configuration()
    pg = PlotGrid(cfg)

    # add and remove a source gracefully
    class FakeSource:
        def get_disp_freq(self):
            return 10

    src = FakeSource()
    pg.add_source(src)
    # method should not raise
    pg.update_plots()

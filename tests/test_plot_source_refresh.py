"""The plot-source selector follows the device's channels.

Enabling or disabling a BIOPAC channel changes which streams exist. The server
now reports the new list in its reply to UPDATE_RUNNING_PARAMETER, the client
republishes it, and the selector is rebuilt -- unplotting whatever has gone away
and keeping the ticks on whatever has not.
"""
import pytest
from bioview_common import DataSource, Response

from bioview_client.components.plot_grid import PlotGrid
from bioview_client.handler import Client


def _src(channel, label, group="BIOPAC"):
    return DataSource(group_id=group, channel=channel, label=label)


# --------------------------------------------------------------------------
# Client: republishing the server's new source list
# --------------------------------------------------------------------------


@pytest.fixture
def client(qapp):
    return Client()


def _reply(client, monkeypatch, payload):
    monkeypatch.setattr(client, "_send_command_locked", lambda **kw: b"stub")
    monkeypatch.setattr(
        "bioview_client.handler.parse_and_validate_response",
        lambda _resp: (Response.SUCCESS.name, payload),
    )


def test_a_parameter_reply_republishes_the_new_source_list(client, monkeypatch):
    sources = [_src(0, "Ch1").to_dict(), _src(1, "Ch2").to_dict()]
    _reply(client, monkeypatch, {"message": "ok", "data_sources": sources})

    seen = []
    client.data_sources_changed.connect(seen.append)

    assert client.configure_device("BIOPAC", {"channels": [1, 1, 0, 0]}) is True
    assert client.get_data_sources() == sources
    assert seen == [sources]


def test_a_reply_without_sources_leaves_the_old_list_alone(client, monkeypatch):
    client.data_sources = ["unchanged"]
    _reply(client, monkeypatch, {"message": "ok"})

    seen = []
    client.data_sources_changed.connect(seen.append)

    assert client.configure_device("BIOPAC", {"samp_rate": 500}) is True
    assert client.data_sources == ["unchanged"]
    assert seen == []


# --------------------------------------------------------------------------
# Monitor: reconciling the grid and the selector with the new list
# --------------------------------------------------------------------------


class _FakeSettingsPanel:
    """Records what the monitor does to the plot-source combo box."""

    def __init__(self):
        self.available = None
        self.ticked = set()

    def set_available_sources(self, sources):
        self.available = list(sources)
        # The real combo box rebuilds its model, clearing every check state.
        self.ticked = set()

    def update_source(self, action, source):
        if action == "add":
            self.ticked.add(source)
        else:
            self.ticked.discard(source)


class _Monitor:
    """Just enough of BioViewMonitor to exercise populate_plot_grid_sources."""

    from bioview_client.monitor import BioViewMonitor

    populate_plot_grid_sources = BioViewMonitor.populate_plot_grid_sources

    def __init__(self, qapp):
        self.plot_grid = PlotGrid(config=None)
        self.settings_panel = _FakeSettingsPanel()
        self.available_sources = []


@pytest.fixture
def monitor(qapp):
    return _Monitor(qapp)


def test_a_new_channel_appears_in_the_selector(monitor):
    monitor.populate_plot_grid_sources([_src(0, "Ch1")])
    assert [s.label for s in monitor.settings_panel.available] == ["Ch1"]

    monitor.populate_plot_grid_sources([_src(0, "Ch1"), _src(1, "Ch2")])
    assert [s.label for s in monitor.settings_panel.available] == ["Ch1", "Ch2"]


def test_a_plotted_source_that_disappears_is_unplotted(monitor):
    ch1, ch2 = _src(0, "Ch1"), _src(1, "Ch2")
    monitor.populate_plot_grid_sources([ch1, ch2])
    monitor.plot_grid.add_source(ch1)
    monitor.plot_grid.add_source(ch2)

    monitor.populate_plot_grid_sources([ch1])

    assert list(monitor.plot_grid.selected_channels) == [ch1]
    # ...and its grid cell is handed back, rather than staying occupied by a
    # channel the device no longer has.
    assert len(monitor.plot_grid.available_slots) == 3


def test_a_surviving_source_keeps_its_tick(monitor):
    ch1, ch2 = _src(0, "Ch1"), _src(1, "Ch2")
    monitor.populate_plot_grid_sources([ch1, ch2])
    monitor.plot_grid.add_source(ch1)
    monitor.settings_panel.update_source("add", ch1)

    monitor.populate_plot_grid_sources([ch1, ch2, _src(2, "Ch3")])

    assert monitor.settings_panel.ticked == {ch1}


def test_disabling_every_channel_clears_the_selector(monitor):
    ch1 = _src(0, "Ch1")
    monitor.populate_plot_grid_sources([ch1])
    monitor.plot_grid.add_source(ch1)

    monitor.populate_plot_grid_sources([])

    assert monitor.settings_panel.available == []
    assert monitor.plot_grid.selected_channels == {}


def test_descriptor_dicts_are_accepted_as_well_as_objects(monitor):
    monitor.populate_plot_grid_sources([_src(0, "Ch1").to_dict()])
    assert [s.label for s in monitor.settings_panel.available] == ["Ch1"]


def test_no_source_list_at_all_is_ignored(monitor):
    monitor.populate_plot_grid_sources([_src(0, "Ch1")])
    monitor.populate_plot_grid_sources(None)
    assert [s.label for s in monitor.settings_panel.available] == ["Ch1"]

"""The plot-source selector must exist even without an Experiment config block.

SettingsPanel builds its CommonSettingsPanel -- which owns the plot-source
selector, the display-duration box and the grid-layout box -- from the
configuration dict. A config file with no EXPERIMENT section used to leave
`experiment_panel` as None, turning set_available_sources() and update_source()
into silent no-ops: no source could be ticked, so nothing could be plotted.
"""

import pytest
from bioview_common import DataSource, ExperimentConfiguration

from bioview_client.components.settings_panel import SettingsPanel


@pytest.fixture
def sources():
    return [
        DataSource(group_id="G", channel=0, label="Tx1Rx1"),
        DataSource(group_id="G", channel=1, label="CalRef_Tx1"),
    ]


def test_experiment_panel_is_built_from_a_default_config(qapp, sources):
    panel = SettingsPanel({"Experiment": ExperimentConfiguration({})})

    assert panel.experiment_panel is not None
    panel.set_available_sources(sources)
    assert panel.experiment_panel.plot_source.model().rowCount() == len(sources)


def test_selection_state_round_trips(qapp, sources):
    panel = SettingsPanel({"Experiment": ExperimentConfiguration({})})
    panel.set_available_sources(sources)

    panel.update_source("add", sources[1])
    # Channel labels are only unique within a device, so the selector names each
    # source as "<device>: <source>" -- the same name the plot title carries.
    assert panel.experiment_panel.plot_source.checkedItemTexts() == ["G: CalRef_Tx1"]

    panel.update_source("remove", sources[1])
    assert panel.experiment_panel.plot_source.checkedItemTexts() == []


def test_monitor_registers_a_default_experiment_config(qapp):
    """A config with only device groups still gets an Experiment settings tab."""
    from bioview_common import DummyConfiguration

    from bioview_client.monitor import split_configurations

    groups = {"Dummy": DummyConfiguration({})}
    configurations, experiment_config, group_configs = split_configurations(groups)

    assert isinstance(experiment_config, ExperimentConfiguration)
    assert "Experiment" in configurations
    assert list(group_configs) == ["Dummy"]

    panel = SettingsPanel(configurations)
    assert panel.experiment_panel is not None


def test_declared_experiment_config_is_kept(qapp):
    from bioview_client.monitor import split_configurations

    declared = ExperimentConfiguration({"file_name": "session.bvr"})
    configurations, experiment_config, group_configs = split_configurations(
        {"MyExperiment": declared}
    )

    assert experiment_config is declared
    assert list(configurations) == ["MyExperiment"]
    assert group_configs == {}

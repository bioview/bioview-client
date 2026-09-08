"""A running balance has to be visible in the panel it is changing.

The search drives phase, amplitude and both analog gains for a minute or more.
The panel used to sit on the values from before the balance started, so there
was no way to tell a search that was working from one doing nothing at all.
"""

import pytest
from bioview_common import USRPConfiguration

from bioview_client.components.settings_panel import SettingsPanel
from bioview_client.components.settings_panel.device_settings import USRPSettingsPanel


CONFIG = {
    "device_type": "usrp",
    "device_name": "Radio",
    "hardware": {
        "A": {
            "tx_channels": [0, 1],
            "rx_channels": [0, 1],
            "if_freq": [100e3, 110e3],
            "tx_gain": [40, 40],
            "rx_gain": [40, 40],
            "tx_amplitude": [1.0, 1.0],
            "tx_phase": [0.0, 0.0],
        }
    },
    "channel_map": {
        "layout": "full_nxn",
        "dpic": [{"inject_tx": 1, "measure_tx": 0, "measure_rx": 0}],
    },
}

PROGRESS = {
    "stage": "coarse phase",
    "point": 12,
    "planned": 60,
    "inject_tx": 1,
    "measure_tx": 0,
    "measure_rx": 0,
    "phase_deg": 66.0,
    "amplitude": 0.1,
    "tx_gain_db": 43.0,
    "rx_gain_db": 45.0,
    "metric": 0.31,
}


@pytest.fixture
def panel(qapp):
    import copy

    return USRPSettingsPanel(USRPConfiguration(copy.deepcopy(CONFIG)))


def test_progress_moves_the_spin_boxes_for_the_right_channels(panel):
    panel.apply_balance_progress(PROGRESS)

    # Inject Tx is global index 1; the measure Tx and Rx are index 0.
    assert panel.param_inputs["tx_phase"][1].value() == pytest.approx(66.0)
    assert panel.param_inputs["tx_amplitude"][1].value() == pytest.approx(0.1)
    assert panel.param_inputs["tx_gain"][0].value() == 43
    assert panel.param_inputs["rx_gain"][0].value() == 45

    # ...and nothing on the channels the balance did not touch.
    assert panel.param_inputs["tx_phase"][0].value() == pytest.approx(0.0)
    assert panel.param_inputs["tx_gain"][1].value() == 40


def test_a_live_value_is_not_echoed_back_to_the_server(panel):
    """Otherwise the UI fights the search, point by point."""
    emitted = []
    panel.device_param_changed.connect(lambda *args: emitted.append(args))

    panel.apply_balance_progress(PROGRESS)

    assert emitted == []


def test_the_configuration_follows_the_hardware(panel):
    """A later manual edit must start from what the radio actually has."""
    from bioview_common.datatypes.configuration.hardware_params import (
        resolve_param_values,
    )

    panel.apply_balance_progress(PROGRESS)

    assert resolve_param_values(panel.device_configuration, "tx_phase")[1] == 66.0
    assert resolve_param_values(panel.device_configuration, "rx_gain")[0] == 45.0


def test_the_button_reports_the_stage_and_returns_when_done(panel):
    panel.apply_balance_progress(PROGRESS)
    assert panel.balance_button.text() == "coarse phase 12/60"

    panel.set_balance_running(False)
    assert panel.balance_button.text() == "Balance"
    assert panel.balance_button.isEnabled()


def test_progress_is_routed_to_the_device_that_is_balancing(qapp):
    import copy

    from bioview_common import ExperimentConfiguration

    panel = SettingsPanel(
        {
            "Experiment": ExperimentConfiguration({}),
            "Radio": USRPConfiguration(copy.deepcopy(CONFIG)),
        }
    )

    panel.apply_balance_progress("Radio", PROGRESS)
    assert panel.setting_widgets["Radio"].balance_button.text() == "coarse phase 12/60"

    # An id with no RF panel behind it is ignored rather than raising.
    panel.apply_balance_progress("Experiment", PROGRESS)
    panel.apply_balance_progress("NoSuchDevice", PROGRESS)

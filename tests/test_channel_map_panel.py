"""The channel map panel must round-trip a DPIC pair without losing measure_rx.

``DpicPair.target_rx`` falls back to ``measure_tx`` when ``measure_rx`` is
absent -- a Tx index used as an Rx index, correct only in a 1x1 layout. The
panel used to build its pair dicts from the two Tx combos alone, so opening the
settings panel on a correct 2x2 config and touching anything at all rewrote the
config into a balance loop that reads the wrong receiver.
"""

import pytest
from bioview_common import USRPConfiguration
from bioview_common.datatypes.configuration.usrp_channel_map import resolve_channel_map

from bioview_client.components.settings_panel.usrp_channel_map_panel import (
    USRPChannelMapPanel,
)


def _config(dpic, if_freq_7=(100e3,)):
    return USRPConfiguration(
        {
            "device_name": "USRP_DPIC_2x2",
            "samp_rate": 1e6,
            "carrier_freq": 1e9,
            "hardware": {
                "MyB210_4": {
                    "tx_channels": [0, 1],
                    "rx_channels": [0, 1],
                    "if_freq": [100e3, 110e3],
                },
                "MyB210_7": {
                    "tx_channels": [0],
                    "rx_channels": [0, 1],
                    "if_freq": list(if_freq_7),
                },
            },
            "channel_map": {
                "layout": "hybrid_mimo",
                "mimo": {"tx_global": [0, 1], "rx_global": [0, 1]},
                "dpic": dpic,
            },
        }
    )


@pytest.fixture
def dpic_pair():
    # Tx2 (global index 2, on MyB210_7) injects at 100 kHz against Tx1/Rx1.
    return [{"inject_tx": 2, "measure_tx": 0, "measure_rx": 0}]


def test_measure_rx_survives_a_round_trip(qapp, dpic_pair):
    panel = USRPChannelMapPanel(_config(dpic_pair))

    emitted = panel.get_channel_map()
    assert emitted["dpic"] == [{"inject_tx": 2, "measure_tx": 0, "measure_rx": 0}]

    # And the resolved pair reads the Rx that was configured, not the Tx index.
    _, _, pairs = resolve_channel_map("USRP_DPIC_2x2", emitted, panel._hardware())
    assert [(p.inject_tx, p.measure_tx, p.target_rx) for p in pairs] == [(2, 0, 0)]


def test_a_new_pair_defaults_to_a_measurable_rx(qapp):
    panel = USRPChannelMapPanel(_config([]))
    panel._add_dpic_row(inject_tx=2, measure_tx=0)

    pair = panel.get_channel_map()["dpic"][0]
    assert pair["measure_rx"] is not None
    # Rx0 is on MyB210_4, the same radio as measure Tx0, and is still in the
    # measurement grid once Tx2's receiver is retired.
    assert pair["measure_rx"] == 0
    assert not panel._dpic_problems(panel.get_channel_map()["dpic"])


def test_mismatched_if_is_reported_before_streaming(qapp, dpic_pair):
    # The inject Tx sits at 120 kHz while the measure Tx is at 100 kHz: the Rx
    # band-pass rejects the tone and no setting can null the direct path.
    panel = USRPChannelMapPanel(_config(dpic_pair, if_freq_7=(120e3,)))

    problems = panel._dpic_problems(panel.get_channel_map()["dpic"])
    assert any("120 kHz" in p and "100 kHz" in p for p in problems)
    # isVisible() needs a shown parent; isHidden() is the panel-local state.
    assert not panel.warning_label.isHidden()


def test_unmeasurable_pair_is_reported(qapp, dpic_pair):
    panel = USRPChannelMapPanel(_config(dpic_pair))
    # Point the loop at an Rx that carries no measurement for the measure Tx.
    panel._dpic_rows[0]["measure_rx"].setCurrentIndex(
        panel._dpic_rows[0]["measure_rx"].findData(2)
    )

    problems = panel._dpic_problems(panel.get_channel_map()["dpic"])
    assert any("not a checked measurement pair" in p for p in problems)


def test_a_custom_map_is_not_promoted_to_a_full_grid(qapp):
    config = _config([])
    config.set_param(
        "channel_map",
        {
            "layout": "custom",
            "pairs": [{"tx": 0, "rx": 0}, {"tx": 1, "rx": 1}],
            "dpic": [],
        },
    )
    panel = USRPChannelMapPanel(config)

    channel_map = panel.get_channel_map()
    assert channel_map["layout"] == "custom"
    assert channel_map["pairs"] == [{"tx": 0, "rx": 0}, {"tx": 1, "rx": 1}]

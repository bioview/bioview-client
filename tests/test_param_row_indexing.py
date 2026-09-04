"""Per-channel settings spinboxes must report their own column index.

``add_param_rows`` used to read ``len(values)`` from the enclosing scope inside
the change callbacks. That name is rebound on each parameter, so callbacks fired
later saw the *last* parameter's length -- with a single-valued parameter last,
every per-channel spinbox reported ``idx=None`` and the handler overwrote the
whole list with a scalar.
"""

import pytest
from PyQt6.QtWidgets import QGridLayout, QWidget

from bioview_client.components.settings_panel.panel_utils import add_param_rows


class _Cfg:
    def __init__(self, values):
        self._values = values

    def get_param(self, name, default=None):
        return self._values.get(name, default)

    def to_dict(self):
        return dict(self._values)


@pytest.fixture
def grid(qapp):
    holder = QWidget()
    return QGridLayout(holder), holder


def test_multi_value_param_reports_its_column(grid):
    layout, _holder = grid
    cfg = _Cfg({"tx_gain": [30.0, 40.0], "carrier_freq": 9e8})
    seen = []

    widgets, _row = add_param_rows(
        layout,
        cfg,
        {
            "tx_gain": ("TX Gain (dB)", (0, 70), 1, 1, 0),
            # A single-valued parameter last is what used to poison the earlier
            # rows' callbacks.
            "carrier_freq": ("Carrier Freq. (MHz)", (30, 6000), 1e6, 1, 1),
        },
        lambda param, value, idx=None: seen.append((param, value, idx)),
    )

    seen.clear()
    widgets["tx_gain"][1].setValue(55)
    assert seen == [("tx_gain", 55.0, 1)]

    seen.clear()
    widgets["carrier_freq"][0].setValue(950.0)
    assert seen == [("carrier_freq", 950.0 * 1e6, None)]

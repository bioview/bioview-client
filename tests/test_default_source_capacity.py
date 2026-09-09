"""A configured display source must actually reach a plot.

`Word_Classification.bvi` lists five `display_sources` -- four RF channels and
`MIC: Audio`. The grid defaults to 2x2, `PlotGrid.add_source` refuses once the
four cells are taken, and `_apply_default_sources` marks a name as honoured
*before* it attempts the add. So the microphone was dropped on the first refresh
and never retried: the plot the operator was told to check before recording
simply did not exist.
"""

import pytest
from bioview_common import DataSource

from bioview_client.components.plot_grid import PlotGrid
from bioview_client.monitor import BioViewMonitor


def test_four_sources_still_get_the_familiar_square():
    assert BioViewMonitor._grid_for(1) == (2, 2)
    assert BioViewMonitor._grid_for(4) == (2, 2)


def test_a_fifth_source_grows_the_grid():
    rows, cols = BioViewMonitor._grid_for(5)
    assert rows * cols >= 5


@pytest.mark.parametrize("count", range(1, 13))
def test_the_grid_holds_everything_the_config_asked_for(count):
    rows, cols = BioViewMonitor._grid_for(count)
    assert rows * cols >= count
    assert rows <= BioViewMonitor.MAX_GRID_ROWS
    assert cols <= BioViewMonitor.MAX_GRID_COLS


def test_the_layout_is_capped_at_the_spin_box_limits():
    """Beyond twelve the grid stops growing; the monitor warns instead of
    silently reporting a layout the settings panel cannot show."""
    rows, cols = BioViewMonitor._grid_for(16)
    assert (rows, cols) == (BioViewMonitor.MAX_GRID_ROWS, BioViewMonitor.MAX_GRID_COLS)


def test_the_word_classification_source_set_fits(qapp):
    """The end of the bug, exercised against a real grid."""
    sources = [
        DataSource(group_id="RF_MIMO_4x4", channel=i, label=f"Tx{i + 1}Rx{i + 1}")
        for i in range(4)
    ] + [DataSource(group_id="MIC", channel=0, label="Audio", disp_freq=16000.0)]

    grid = PlotGrid(config=None)
    # What the monitor does at startup, before any source is advertised.
    rows, cols = BioViewMonitor._grid_for(len(sources))
    grid.update_grid(rows, cols)

    assert all(grid.add_source(source) for source in sources)
    assert sources[-1] in grid.selected_channels


def test_the_default_grid_would_have_dropped_the_microphone(qapp):
    """Guards the regression itself: 2x2 is genuinely too small for five."""
    sources = [
        DataSource(group_id="RF_MIMO_4x4", channel=i, label=f"Tx{i + 1}Rx{i + 1}")
        for i in range(4)
    ] + [DataSource(group_id="MIC", channel=0, label="Audio")]

    grid = PlotGrid(config=None)
    assert (grid.rows, grid.cols) == (2, 2)
    results = [grid.add_source(source) for source in sources]
    assert results[-1] is False

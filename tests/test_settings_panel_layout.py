"""The settings panels sit side by side, sized from the window's width.

They used to be tabs: one instrument visible at a time, and a tab bar plus its
padding costing height the plots could have had. The strip shows as many as the
width can carry (1 / 2 / 3 / 4 across the breakpoints) and scrolls sideways for
the rest, with each visible column an exact share of the viewport so the strip
ends flush with the window edge.
"""

import pytest
from bioview_common import DummyConfiguration, ExperimentConfiguration

from bioview_client.components.settings_panel import SettingsPanel


def _panel(count):
    configurations = {"Experiment": ExperimentConfiguration({})}
    for i in range(count - 1):
        configurations[f"Dummy{i}"] = DummyConfiguration({})
    return SettingsPanel(configurations)


@pytest.mark.parametrize(
    ("width", "columns"),
    [(800, 1), (1200, 2), (1600, 3), (2400, 4)],
)
def test_columns_follow_the_width(qapp, width, columns):
    assert _panel(6)._visible_columns(width) == columns


def test_cards_divide_the_viewport_exactly(qapp):
    panel = _panel(6)
    panel.resize(1600, 200)
    panel.show()
    qapp.processEvents()

    viewport = panel._strip.viewport().width()
    widths = {card.width() for card in panel._cards}
    assert len(widths) == 1

    # Three columns at 1600 px: the three visible cards plus their two gaps
    # fill the viewport, to within the integer division.
    card_width = widths.pop()
    assert abs(card_width * 3 + panel.CARD_SPACING * 2 - viewport) <= 3


def test_fewer_panels_than_columns_share_the_whole_strip(qapp):
    panel = _panel(2)
    panel.resize(2400, 200)
    panel.show()
    qapp.processEvents()

    viewport = panel._strip.viewport().width()
    total = sum(card.width() for card in panel._cards) + panel.CARD_SPACING
    # Two panels on an extra-large window stretch across it rather than sitting
    # at a quarter width each with half the strip empty.
    assert abs(total - viewport) <= 3


def test_every_configuration_gets_a_panel_and_keeps_its_id(qapp):
    panel = _panel(3)
    assert set(panel.setting_widgets) == {"Experiment", "Dummy0", "Dummy1"}
    # The group box carries the identity the tab bar used to.
    assert panel.setting_widgets["Dummy0"].title() == "Dummy0 Settings"
    assert panel.experiment_panel is not None


# --------------------------------------------------------------------------
# Configured shares: "panel_width" overrides the equal-column default
# --------------------------------------------------------------------------


def _weighted_panel(shares):
    """A strip whose panels each claim ``shares[i]`` of the width (None = none)."""
    configurations = {}
    for i, share in enumerate(shares):
        cfg = {} if share is None else {"panel_width": share}
        configurations[f"Dummy{i}"] = DummyConfiguration(cfg)
    return SettingsPanel(configurations)


def _shown(panel, width):
    panel.resize(width, 200)
    panel.show()
    panel._relayout_cards()
    return panel._strip.viewport().width()


def test_configured_shares_divide_the_strip(qapp):
    panel = _weighted_panel([0.25, 0.5, 0.25])
    viewport = _shown(panel, 1600)

    widths = [card.width() for card in panel._cards]
    usable = viewport - panel.CARD_SPACING * 2
    assert abs(widths[0] - usable * 0.25) <= 2
    assert abs(widths[1] - usable * 0.5) <= 2
    assert abs(widths[2] - usable * 0.25) <= 2
    # Every panel is on screen: naming shares only means something if they are
    # all visible at once, so the breakpoint columns do not apply.
    assert abs(sum(widths) + panel.CARD_SPACING * 2 - viewport) <= 3


def test_shares_need_not_sum_to_one(qapp):
    panel = _weighted_panel([1, 3])
    _shown(panel, 1600)

    narrow, wide = (card.width() for card in panel._cards)
    assert abs(wide - 3 * narrow) <= 3


def test_a_panel_without_a_share_takes_the_average_of_those_that_have_one(qapp):
    panel = _weighted_panel([0.2, 0.6, None])
    _shown(panel, 1600)

    widths = [card.width() for card in panel._cards]
    # The unannotated panel sits between the two that spoke up, rather than
    # collapsing to nothing or swallowing the strip.
    assert widths[0] < widths[2] < widths[1]


@pytest.mark.parametrize("share", ["wide", 0, -1, None])
def test_an_unusable_share_leaves_the_equal_columns_alone(qapp, share):
    panel = _weighted_panel([share, share])
    _shown(panel, 1600)

    widths = {card.width() for card in panel._cards}
    assert len(widths) == 1

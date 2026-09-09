"""The bar reports state changes, and stops feeling like a control.

Two separate complaints, one widget. Initialization used to happen entirely in
the log window -- closed by default -- so a rig that took ninety seconds to come
up looked identical to one that had ignored the button. And the strip lit up
under the cursor as if the whole thing were clickable.
"""

import pytest
from bioview_common import DeviceStatus
from PyQt6.QtCore import Qt

from bioview_client.components.status_bar import StatusBar


@pytest.fixture
def status_bar(qapp):
    from PyQt6.QtWidgets import QMainWindow

    window = QMainWindow()
    bar = StatusBar(device_status={"BIOPAC": DeviceStatus.NOINIT}, parent=window)
    window.setStatusBar(bar)
    window.resize(1200, 800)
    # The window owns the bar in C++; without a Python reference it is
    # collected at the end of the fixture and takes the bar's widgets with it.
    bar._test_window = window
    yield bar
    window.close()


def test_an_activity_message_is_shown_in_the_bar(status_bar):
    status_bar.show_activity("Initialization started…")
    assert status_bar.activity_label.text() == "Initialization started…"


def test_a_running_operation_does_not_time_itself_out(status_bar):
    """An init can run for a minute; the message has to outlast it."""
    status_bar.show_activity("Initialization started…", level="info")
    assert not status_bar._activity_timer.isActive()


def test_a_finished_state_clears_itself(status_bar):
    for level in ("success", "warning", "error"):
        status_bar.show_activity("done", level=level)
        assert status_bar._activity_timer.isActive()
        assert status_bar._activity_timer.interval() == status_bar.ACTIVITY_TIMEOUT_MS
        status_bar.clear_activity()
        assert status_bar.activity_label.text() == ""


def test_the_last_message_wins(status_bar):
    status_bar.show_activity("Initialization started…", level="info")
    status_bar.show_activity("Initialized successfully!", level="success")
    assert status_bar.activity_label.text() == "Initialized successfully!"
    assert status_bar._activity_timer.isActive()


def test_the_readouts_do_not_take_the_mouse(status_bar):
    """Nothing that merely states a fact may enter a hover state."""
    inert = (
        status_bar.server_indicator,
        status_bar.server_status_label,
        status_bar.activity_label,
        status_bar.routine_progress,
        status_bar.device_status_panel,
    )
    for widget in inert:
        assert widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_buttons_stay_clickable(status_bar):
    """Removing the hover must not disarm the one control in the bar."""
    assert status_bar.more_button.isEnabled()
    assert not status_bar.more_button.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )

    opened = []
    status_bar.open_server_flyout = lambda: opened.append(True)
    status_bar.more_button.clicked.disconnect()
    status_bar.more_button.clicked.connect(status_bar.open_server_flyout)
    status_bar.more_button.click()
    assert opened == [True]


def test_routine_states_share_a_neutral_colour(status_bar):
    """Green means something went right and red means it did not. Starting a
    stream, and any plain notice, is just what this application does, so both
    report in the same neutral yellow instead of a colour that claims more."""
    streaming = StatusBar.activity_color("streaming")
    success = StatusBar.activity_color("success")
    info = StatusBar.activity_color("info")
    error = StatusBar.activity_color("error")

    assert info.name() == streaming.name()
    assert streaming.name() not in {success.name(), error.name()}
    # Yellow: red and green both high, blue low.
    for colour in (streaming, info):
        r, g, b, _ = colour.getRgb()
        assert r > 120 and g > 100 and b < 90


def test_streaming_is_muted_against_the_raw_palette_yellow(status_bar):
    from bioview_client.constants.theme import get_qcolor

    raw = get_qcolor("yellow")
    for level in ("streaming", "info"):
        muted = StatusBar.activity_color(level)
        assert sum(muted.getRgb()[:3]) < sum(raw.getRgb()[:3])


def test_a_streaming_message_expires_like_any_finished_state(status_bar):
    status_bar.show_activity("Streaming started", level="streaming")
    assert status_bar.activity_label.text() == "Streaming started"
    assert status_bar._activity_timer.isActive()


def test_the_only_control_looks_like_a_control(status_bar):
    """A flat button paints a bare rectangle of hover into an otherwise empty
    strip, which is what made the *bar* look like the thing lighting up."""
    assert not status_bar.more_button.isFlat()


def _widgets_that_repaint_on_hover(qapp, bar):
    """Every widget in ``bar`` whose appearance changes under the cursor.

    Qt derives ``State_MouseOver`` from ``WA_UnderMouse``, so setting that
    attribute and re-rendering reproduces a real hover without a real mouse --
    which synthetic QHoverEvents do not. Two rounds of guessing at this cost
    more than one round of measuring it.
    """
    from PyQt6.QtWidgets import QWidget

    baseline = bar.grab().toImage()

    def changed():
        shot = bar.grab().toImage()
        return any(
            shot.pixel(x, y) != baseline.pixel(x, y)
            for y in range(shot.height())
            for x in range(shot.width())
        )

    candidates = [bar]
    stack = [bar]
    while stack:
        for child in stack.pop().children():
            if isinstance(child, QWidget) and child.isVisible():
                candidates.append(child)
                stack.append(child)

    repainting = []
    for widget in candidates:
        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
        widget.update()
        qapp.processEvents()
        if changed():
            repainting.append(widget)
        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        widget.update()
        qapp.processEvents()
    return repainting


def test_nothing_but_the_buttons_lights_up_under_the_cursor(qapp, status_bar):
    """The complaint, as an assertion.

    Not a synthetic QHoverEvent: those are delivered but do not set the
    under-mouse state the style paints from, so they report no change however
    broken the bar is.
    """
    from PyQt6.QtWidgets import QAbstractButton

    status_bar.show_activity("Streaming started", level="streaming")
    qapp.processEvents()

    offenders = [
        w
        for w in _widgets_that_repaint_on_hover(qapp, status_bar)
        if not isinstance(w, QAbstractButton)
    ]
    assert offenders == [], (
        "these repaint on hover without being buttons: "
        f"{[type(w).__name__ for w in offenders]}"
    )


def test_the_size_grip_is_gone(status_bar):
    """The grip is the one part of the bar that lights up without doing
    anything anyone wants; the window resizes from its edges regardless."""
    assert not status_bar.isSizeGripEnabled()

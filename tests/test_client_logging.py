"""What the client puts in the log window.

The log used to report that something failed without saying what or why: a
device that would not initialize produced "no devices connected" and nothing
else, while the actual cause stayed on the server.
"""

import logging
import re

from bioview_client.components.log_display import (
    LEVEL_COLORS,
    HtmlLogFormatter,
    LogDisplayPanel,
)
from bioview_client.handler import _describe_params, _describe_response


class TestCommandTrace:
    """Debug tracing summarises each control exchange without dumping payloads."""

    def test_a_large_config_is_summarised_not_dumped(self):
        params = {"device_groups": {f"dev{i}": {"junk": "x" * 500} for i in range(9)}}
        line = _describe_params(params)
        assert "9 key(s)" in line
        assert "x" * 200 not in line, "a log window full of config is unreadable"

    def test_scalar_parameters_are_shown_as_they_are(self):
        assert "timeout=30" in _describe_params({"timeout": 30})

    def test_no_parameters_renders_nothing(self):
        assert _describe_params(None) == ""
        assert _describe_params({}) == ""

    def test_a_long_scalar_is_truncated(self):
        line = _describe_params({"path": "y" * 400})
        assert line.endswith("...)")
        assert len(line) < 200

    def test_a_lost_connection_is_named_not_left_blank(self):
        assert "no response" in _describe_response(None)

    def test_an_unreadable_reply_is_reported_with_its_size(self):
        assert "unreadable response" in _describe_response(b"not a bioview frame")

    def test_a_server_error_message_is_surfaced_in_the_trace(self):
        import json

        raw = json.dumps(
            {
                "type": "ERROR",
                "payload": {"message": "BIOPAC connection failed: MPDRVERR (code 2)"},
            }
        ).encode()
        line = _describe_response(raw)
        assert "ERROR" in line
        assert "MPDRVERR" in line


class TestLogPanelFormatting:
    """One panel serves both windows, so its formatting is tested once."""

    @staticmethod
    def _render(level, message):
        record = logging.LogRecord(
            name="test",
            level=getattr(logging, level.upper()),
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )
        return HtmlLogFormatter().format(record)

    def test_each_line_carries_a_timestamp_and_level(self):
        line = self._render("error", "device failed")
        assert "[ERROR]" in line
        assert "device failed" in line
        assert re.search(r"\d{2}:\d{2}:\d{2}", line), "expected an HH:MM:SS stamp"

    def test_each_level_is_coloured_distinctly(self):
        assert LEVEL_COLORS["error"] in self._render("error", "x")
        assert LEVEL_COLORS["warning"] in self._render("warning", "x")
        assert LEVEL_COLORS["debug"] in self._render("debug", "x")

    def test_the_level_colours_are_the_agreed_ones(self):
        """Grey for debug, blue for info, red for error: pinned here because the
        colour is how the level is read at a glance while a recording runs."""
        assert LEVEL_COLORS["debug"] == "#9aa0a6", "grey"
        assert LEVEL_COLORS["info"] == "#8ecae6", "blue"
        assert LEVEL_COLORS["error"] == "#ff6b6b", "red"
        assert LEVEL_COLORS["critical"] == LEVEL_COLORS["error"]
        assert LEVEL_COLORS["warning"] == "#ffd166", "amber, between info and error"

    def test_markup_in_a_message_is_escaped_rather_than_rendered(self):
        assert "&lt;B210&gt;" in self._render("info", "found <B210> device")

    def test_the_warn_spelling_is_normalised_so_it_is_coloured(self):
        """Call sites use both spellings; the colour table is keyed on "warning"."""
        recorded = []

        class FakeLogger:
            def warning(self, msg):
                recorded.append(("warning", msg))

            def info(self, msg):
                recorded.append(("info", msg))

        panel = LogDisplayPanel.__new__(LogDisplayPanel)
        panel.logger = FakeLogger()
        LogDisplayPanel.log_message(panel, "warn", "careful")

        assert recorded == [("warning", "careful")]

    def test_an_unknown_level_still_records_the_message(self):
        recorded = []

        class FakeLogger:
            def info(self, msg):
                recorded.append(msg)

        panel = LogDisplayPanel.__new__(LogDisplayPanel)
        panel.logger = FakeLogger()
        LogDisplayPanel.log_message(panel, "shout", "still important")

        assert recorded == ["still important"]

    def test_the_configurator_name_for_the_method_still_works(self):
        """The Configurator called this add_log_message; both names are kept."""
        assert LogDisplayPanel.add_log_message is LogDisplayPanel.log_message

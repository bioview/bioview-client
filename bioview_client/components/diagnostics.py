"""One modal for every server-level fault, shared by both windows.

A backend that will not load and a UHD that does not match its bindings are
the same kind of problem wherever they surface: the server knows about them,
neither GUI can do anything about them, and the operator has to be told before
spending ten minutes wondering why a radio is missing. The wording comes from
``bioview_common.diagnostics``; this is only how it is put on screen.

The Monitor and the Configurator differ in *which* faults concern them -- see
:func:`relevant_diagnostics` -- but not in how they show them.
"""

from __future__ import annotations

import contextlib

from PyQt6.QtWidgets import QMessageBox


def relevant_diagnostics(issues, device_types=None) -> list[dict]:
    """The issues a window should raise, given the device types it cares about.

    ``device_types`` of ``None`` means "all of them", which is the
    Configurator: its whole job is the hardware, so a backend that did not
    load is always its business. The Monitor passes the types its loaded
    configuration actually uses -- a missing BIOPAC driver is not worth a
    modal in front of someone running two radios and no BIOPAC.
    """
    if device_types is None:
        return list(issues or [])

    wanted = {str(t).lower() for t in device_types}
    return [
        issue
        for issue in (issues or [])
        if str(issue.get("device_type", "")).lower() in wanted
    ]


class DiagnosticsReporter:
    """Shows each distinct fault once per window, however often it is reported.

    The client re-publishes diagnostics on every successful connection, and a
    window that reconnects to a server three times must not put the same
    unloadable backend in front of the user three times.
    """

    def __init__(self, parent=None):
        self.parent = parent
        self._seen: set[str] = set()

    def reset(self):
        """Forget what has been shown, so a real change is reported again."""
        self._seen.clear()

    def report(self, issues, device_types=None) -> list[dict]:
        """Show the issues this window has not already shown. Returns those."""
        fresh = [
            issue
            for issue in relevant_diagnostics(issues, device_types)
            if issue.get("id") not in self._seen
        ]
        if not fresh:
            return []

        for issue in fresh:
            self._seen.add(issue.get("id"))

        with contextlib.suppress(Exception):
            self._show(fresh)
        return fresh

    def _show(self, issues: list[dict]):
        box = QMessageBox(self.parent)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Server problem")
        box.setText(
            issues[0]["title"]
            if len(issues) == 1
            else f"{len(issues)} problems were reported by the server."
        )
        # The advice is the point of the dialog, so it goes in the body rather
        # than behind "Show Details"; the raw error goes behind it, because it
        # is what a bug report needs and not what the operator has to read.
        box.setInformativeText(
            "\n\n".join(
                (
                    issue["message"]
                    if len(issues) == 1
                    else f"{issue['title']}\n    {issue['message']}"
                )
                for issue in issues
            )
        )
        detail = "\n\n".join(
            f"{issue.get('device_type', 'server')}: {issue.get('detail', '')}"
            for issue in issues
        ).strip()
        if detail:
            box.setDetailedText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

"""USRP MIMO channel map configuration panel."""

from __future__ import annotations

from bioview_common.datatypes.configuration.usrp_channel_map import (
    build_global_registry,
    inject_rx_indices,
    resolve_channel_map,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bioview_client.constants import get_qcolor


class USRPChannelMapPanel(QGroupBox):
    """Checkbox MIMO matrix + DPIC pair list with live label preview."""

    channel_map_changed = pyqtSignal(dict)

    def __init__(self, device_configuration, parent=None):
        super().__init__("Channel Map", parent)
        self.device_configuration = device_configuration
        self._locked = False
        self._rebuilding = False
        self._matrix_checks = []
        self._dpic_rows = []
        self._tx_global = []
        self._rx_global = []
        self._init_ui()
        self._load_from_config()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(12)

        matrix_col = QVBoxLayout()
        matrix_col.setSpacing(4)
        matrix_col.addWidget(self._section_label("Measurement pairs"))
        self.matrix_widget = QWidget()
        self.matrix_layout = QGridLayout(self.matrix_widget)
        self.matrix_layout.setContentsMargins(0, 0, 0, 0)
        self.matrix_layout.setSpacing(4)
        matrix_col.addWidget(self.matrix_widget)
        matrix_col.addStretch(1)
        layout.addLayout(matrix_col)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        dpic_col = QVBoxLayout()
        dpic_col.setSpacing(4)

        dpic_header = QHBoxLayout()
        dpic_header.addWidget(self._section_label("DPIC cancellation loops"))
        self.add_dpic_btn = QPushButton("+ Add loop")
        self.add_dpic_btn.setToolTip(
            "Add a direct-path interference cancellation loop.\n"
            "The inject Tx radiates a nulling tone at the measure Tx's IF; the "
            "balance search reads the residual on the measure Tx/Rx source."
        )
        self.add_dpic_btn.clicked.connect(lambda: self._add_dpic_row())
        dpic_header.addWidget(self.add_dpic_btn)
        dpic_header.addStretch(1)
        dpic_col.addLayout(dpic_header)

        self.dpic_container = QVBoxLayout()
        self.dpic_container.setSpacing(2)
        dpic_col.addLayout(self.dpic_container)

        self.dpic_empty_label = QLabel("No loops configured.")
        self.dpic_empty_label.setStyleSheet("color: gray;")
        dpic_col.addWidget(self.dpic_empty_label)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {get_qcolor('red').name()};")
        self.warning_label.setVisible(False)
        dpic_col.addWidget(self.warning_label)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("color: gray;")
        dpic_col.addWidget(self.preview_label)
        dpic_col.addStretch(1)
        layout.addLayout(dpic_col, 1)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _hardware(self) -> dict:
        hw = self.device_configuration.get_param("hardware")
        if hw:
            return dict(hw)
        name = self.device_configuration.get_param("device_name") or "Device"
        return {
            name: {
                "tx_channels": self.device_configuration.get_param(
                    "tx_channels", [0, 1]
                ),
                "rx_channels": self.device_configuration.get_param(
                    "rx_channels", [0, 1]
                ),
                "if_freq": self.device_configuration.get_param(
                    "if_freq", [100e3, 110e3]
                ),
            }
        }

    def _registry(self):
        return build_global_registry(self._hardware())

    def _tx_item(self, registry, t: int) -> tuple[str, str]:
        """Label and tooltip for one global Tx index.

        DPIC pairs index the *global* Tx list, which is not the Tx1..N numbering
        the plots use once injectors are retired. The IF rides in the label
        because matching IFs is the one rule a loop must satisfy; the radio port
        stays in the tooltip so several loops still fit on a row.
        """
        device, channel = registry.tx_entries[t]
        if_khz = registry.tx_if_freq[t] / 1e3
        return f"Tx{t + 1} · {if_khz:g} kHz", f"{device} ch{channel} @ {if_khz:g} kHz"

    def _rx_item(self, registry, r: int) -> tuple[str, str]:
        device, channel = registry.rx_entries[r]
        return f"Rx{r + 1}", f"{device} ch{channel}"

    @staticmethod
    def _add_item(combo: QComboBox, label: str, tooltip: str, data) -> None:
        combo.addItem(label, data)
        combo.setItemData(combo.count() - 1, tooltip, Qt.ItemDataRole.ToolTipRole)

    def _measurement_channels(self, dpic: list[dict]):
        """Tx/Rx still carrying measurements once ``dpic`` retires its injectors.

        Mirrors ``_measurement_tx_rx_sets`` on the server, so the matrix a user
        sees is the grid the device will actually produce.
        """
        registry = self._registry()
        inject_txs = {p["inject_tx"] for p in dpic}
        inject_rxs = inject_rx_indices({"dpic": dpic}, registry)
        return (
            [t for t in range(registry.num_tx) if t not in inject_txs],
            [r for r in range(registry.num_rx) if r not in inject_rxs],
        )

    def _dpic_config(self) -> list[dict]:
        """The DPIC pair list exactly as the server consumes it.

        ``measure_rx`` is written out explicitly: omitting it makes the server
        fall back to ``measure_tx``, reading a Tx index as an Rx index, which is
        only ever correct in a 1x1 layout.
        """
        return [
            {
                "inject_tx": row["inject"].currentData(),
                "measure_tx": row["measure"].currentData(),
                "measure_rx": row["measure_rx"].currentData(),
            }
            for row in self._dpic_rows
        ]

    def _refresh_matrix(self):
        """Resize the Tx/Rx matrix for the current DPIC pairs.

        Adding a pair retires the inject Tx *and* the Rx sharing its channel,
        so the grid shrinks the moment a pair is added and grows back when one
        is removed. Checked pairs that survive keep their state.
        """
        tx_global, rx_global = self._measurement_channels(self._dpic_config())
        if tx_global == self._tx_global and rx_global == self._rx_global:
            return
        keep = (
            {(t, r) for row in self._matrix_checks for t, r, cb in row if cb.isChecked()}
            if self._matrix_checks
            else None
        )
        self._rebuild_matrix(tx_global, rx_global, keep)

    def _load_from_config(self):
        channel_map = self.device_configuration.get_param("channel_map") or {}
        dpic = channel_map.get("dpic", [])
        tx_global, rx_global = self._measurement_channels(dpic)
        layout = channel_map.get("layout")
        keep = None

        if layout == "hybrid_mimo":
            mimo = channel_map.get("mimo", {})
            listed_tx = mimo.get("tx_global")
            listed_rx = mimo.get("rx_global")
            if listed_tx is not None:
                tx_global = [t for t in tx_global if t in set(listed_tx)]
            if listed_rx is not None:
                rx_global = [r for r in rx_global if r in set(listed_rx)]
        elif layout == "custom":
            # A custom map names its pairs one by one; the grid has to come back
            # with exactly those ticked, or reopening the panel silently
            # promotes the map to a full hybrid_mimo grid.
            keep = {(p["tx"], p["rx"]) for p in channel_map.get("pairs", [])}

        self._rebuild_matrix(tx_global, rx_global, keep)

        self._rebuilding = True
        try:
            for pair in dpic:
                self._add_dpic_row(
                    pair.get("inject_tx", 0),
                    pair.get("measure_tx", 0),
                    pair.get("measure_rx"),
                )
        finally:
            self._rebuilding = False
        self._refresh_dpic_state()

    def _rebuild_matrix(self, tx_global, rx_global, keep_checked=None):
        # Tearing the grid down retoggles every checkbox; without this guard the
        # teardown alone would emit a channel map per removed widget.
        was_rebuilding = self._rebuilding
        self._rebuilding = True
        try:
            while self.matrix_layout.count():
                item = self.matrix_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._matrix_checks = []

            self.matrix_layout.addWidget(QLabel(""), 0, 0)
            for col, t in enumerate(tx_global):
                self.matrix_layout.addWidget(QLabel(f"Tx{t + 1}"), 0, col + 1)

            for row, r in enumerate(rx_global):
                self.matrix_layout.addWidget(QLabel(f"Rx{r + 1}"), row + 1, 0)
                row_checks = []
                for col, t in enumerate(tx_global):
                    cb = QCheckBox()
                    cb.setChecked(keep_checked is None or (t, r) in keep_checked)
                    cb.setEnabled(not self._locked)
                    cb.stateChanged.connect(self._emit_channel_map)
                    self.matrix_layout.addWidget(cb, row + 1, col + 1)
                    row_checks.append((t, r, cb))
                self._matrix_checks.append(row_checks)

            self._tx_global = list(tx_global)
            self._rx_global = list(rx_global)
        finally:
            self._rebuilding = was_rebuilding

    def _add_dpic_row(self, inject_tx=0, measure_tx=0, measure_rx=None):
        registry = self._registry()
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        # The combos list every global channel and never rebuild: the valid
        # subset depends on the pair list itself, so filtering them here would
        # make one edit yank the options out from under the next.
        inject_cb = QComboBox()
        measure_cb = QComboBox()
        for t in range(registry.num_tx):
            label, tooltip = self._tx_item(registry, t)
            self._add_item(inject_cb, label, tooltip, t)
            self._add_item(measure_cb, label, tooltip, t)
        measure_rx_cb = QComboBox()
        for r in range(registry.num_rx):
            label, tooltip = self._rx_item(registry, r)
            self._add_item(measure_rx_cb, label, tooltip, r)

        inject_cb.setToolTip("Tx that radiates the cancellation tone")
        measure_cb.setToolTip("Tx whose direct path is being cancelled")
        measure_rx_cb.setToolTip(
            "Rx the residual is measured on. Must be an Rx that the "
            "measurement grid still keeps for the measure Tx."
        )

        self._select_data(inject_cb, inject_tx)
        self._select_data(measure_cb, measure_tx)
        if measure_rx is None:
            measure_rx = self._default_measure_rx(registry, measure_cb.currentData())
        self._select_data(measure_rx_cb, measure_rx)

        remove_btn = QPushButton("✕")
        remove_btn.setToolTip("Remove this loop")
        remove_btn.setFixedWidth(24)

        # Reads as a sentence -- inject on one Tx, null it out of one Tx/Rx
        # source -- kept terse so several loops fit the tab without scrolling.
        row_layout.addWidget(QLabel("Inject"))
        row_layout.addWidget(inject_cb)
        row_layout.addWidget(QLabel("→"))
        row_layout.addWidget(measure_cb)
        row_layout.addWidget(QLabel("on"))
        row_layout.addWidget(measure_rx_cb)
        row_layout.addWidget(remove_btn)
        row_layout.addStretch(1)

        self.dpic_container.addWidget(row_widget)
        entry = {
            "widget": row_widget,
            "inject": inject_cb,
            "measure": measure_cb,
            "measure_rx": measure_rx_cb,
            "remove": remove_btn,
        }
        self._dpic_rows.append(entry)

        inject_cb.currentIndexChanged.connect(self._on_dpic_changed)
        measure_cb.currentIndexChanged.connect(self._on_dpic_changed)
        measure_rx_cb.currentIndexChanged.connect(self._on_dpic_changed)
        remove_btn.clicked.connect(lambda: self._remove_dpic_row(entry))
        self._on_dpic_changed()

    @staticmethod
    def _select_data(combo: QComboBox, value):
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else max(0, combo.count() - 1))

    def _default_measure_rx(self, registry, measure_tx) -> int:
        """First Rx on the same radio as the measure Tx, else Rx0.

        A measure Tx and its co-located Rx are the usual wiring, and it keeps a
        freshly added loop valid without the user having to work out the global
        Rx numbering first.
        """
        if measure_tx is None or measure_tx >= registry.num_tx:
            return 0
        device, _ = registry.tx_entries[measure_tx]
        for r, (rx_device, _) in enumerate(registry.rx_entries):
            if rx_device == device:
                return r
        return 0

    def _remove_dpic_row(self, entry):
        if entry in self._dpic_rows:
            self._dpic_rows.remove(entry)
            entry["widget"].setParent(None)
            entry["widget"].deleteLater()
            self._on_dpic_changed()

    def _on_dpic_changed(self):
        """A pair changed: resize the measurement grid, then publish."""
        if self._rebuilding:
            return
        self._refresh_matrix()
        self._emit_channel_map()

    def _dpic_problems(self, dpic: list[dict]) -> list[str]:
        """Client-side mirror of the backend's ``_validate_dpic_pairs``.

        The server logs these at start-up, by which point a session is already
        running on a loop that cannot converge. Same checks, raised while the
        map is still being edited.
        """
        registry = self._registry()
        measurable = {
            (t, r) for row in self._matrix_checks for t, r, cb in row if cb.isChecked()
        }
        problems = []
        for i, pair in enumerate(dpic, start=1):
            inject_tx = pair["inject_tx"]
            measure_tx = pair["measure_tx"]
            measure_rx = pair["measure_rx"]
            if inject_tx is None or measure_tx is None or measure_rx is None:
                continue
            if inject_tx == measure_tx:
                problems.append(
                    f"Loop {i}: inject and measure are both Tx{inject_tx + 1}; "
                    "a channel cannot cancel itself."
                )
                continue
            inject_if = registry.tx_if_freq[inject_tx]
            measure_if = registry.tx_if_freq[measure_tx]
            if abs(inject_if - measure_if) > 1e-6:
                problems.append(
                    f"Loop {i}: inject Tx{inject_tx + 1} is at {inject_if / 1e3:g} kHz "
                    f"but measure Tx{measure_tx + 1} is at {measure_if / 1e3:g} kHz. "
                    "The Rx band-pass rejects the tone, so no setting can cancel "
                    "the direct path — put both Tx on the same IF."
                )
            if (measure_tx, measure_rx) not in measurable:
                problems.append(
                    f"Loop {i}: Tx{measure_tx + 1}/Rx{measure_rx + 1} is not a "
                    "checked measurement pair, so the balance search has no "
                    "signal to read."
                )
        return problems

    def _build_channel_map(self) -> dict:
        tx_global = list(self._tx_global)
        rx_global = list(self._rx_global)
        dpic = self._dpic_config()
        pairs = []
        for row in self._matrix_checks:
            for t, r, cb in row:
                if cb.isChecked():
                    pairs.append({"tx": t, "rx": r})

        if len(pairs) == len(tx_global) * len(rx_global):
            layout = "hybrid_mimo"
            channel_map = {
                "layout": layout,
                "mimo": {"tx_global": tx_global, "rx_global": rx_global},
                "dpic": dpic,
            }
        else:
            layout = "custom"
            channel_map = {
                "layout": layout,
                "pairs": pairs,
                "dpic": dpic,
            }
        return channel_map

    def _refresh_dpic_state(self):
        """Update preview, warnings and the empty-state hint together."""
        channel_map = self._build_channel_map()
        group_id = self.device_configuration.get_param("device_name") or "USRP"
        sources, _, dpic = resolve_channel_map(group_id, channel_map, self._hardware())
        labels = sorted([s.label for s in sources])
        source_txt = ", ".join(labels) if labels else "—"
        dpic_txt = ", ".join(
            f"Tx{p.inject_tx + 1}→Tx{p.measure_tx + 1}/Rx{p.target_rx + 1}" for p in dpic
        )
        self.preview_label.setText(
            f"Sources ({len(labels)}): {source_txt}"
            + (f"\nDPIC: {dpic_txt}" if dpic_txt else "")
        )

        self.dpic_empty_label.setVisible(not self._dpic_rows)
        problems = self._dpic_problems(channel_map["dpic"])
        self.warning_label.setText("\n".join(problems))
        self.warning_label.setVisible(bool(problems))

    # Kept under the old name so existing callers keep working.
    _update_preview = _refresh_dpic_state

    def _emit_channel_map(self):
        if self._locked or self._rebuilding:
            return
        channel_map = self._build_channel_map()
        self._refresh_dpic_state()
        self.channel_map_changed.emit(channel_map)

    def set_streaming_locked(self, locked: bool):
        self._locked = locked
        self.add_dpic_btn.setEnabled(not locked)
        for row in self._matrix_checks:
            for _, _, cb in row:
                cb.setEnabled(not locked)
        for entry in self._dpic_rows:
            entry["inject"].setEnabled(not locked)
            entry["measure"].setEnabled(not locked)
            entry["measure_rx"].setEnabled(not locked)
            entry["remove"].setEnabled(not locked)

    def get_channel_map(self) -> dict:
        return self._build_channel_map()

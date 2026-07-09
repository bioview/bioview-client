"""USRP MIMO channel map configuration panel."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bioview_common.datatypes.configuration.usrp_channel_map import (
    resolve_channel_map,
)


class USRPChannelMapPanel(QGroupBox):
    """Checkbox MIMO matrix + DPIC pair list with live label preview."""

    channel_map_changed = pyqtSignal(dict)

    def __init__(self, device_configuration, parent=None):
        super().__init__("Channel Map", parent)
        self.device_configuration = device_configuration
        self._locked = False
        self._matrix_checks = []
        self._dpic_rows = []
        self._init_ui()
        self._load_from_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        self.matrix_widget = QWidget()
        self.matrix_layout = QGridLayout(self.matrix_widget)
        layout.addWidget(self.matrix_widget)

        dpic_box = QGroupBox("DPIC Pairs")
        dpic_layout = QVBoxLayout(dpic_box)
        self.dpic_container = QVBoxLayout()
        dpic_layout.addLayout(self.dpic_container)

        add_row = QHBoxLayout()
        self.add_dpic_btn = QPushButton("+ Add pair")
        self.add_dpic_btn.clicked.connect(self._add_dpic_row)
        add_row.addWidget(self.add_dpic_btn)
        add_row.addStretch()
        dpic_layout.addLayout(add_row)
        layout.addWidget(dpic_box)

    def _hardware(self) -> dict:
        hw = self.device_configuration.get_param("hardware")
        if hw:
            return dict(hw)
        name = self.device_configuration.get_param("device_name") or "Device"
        return {
            name: {
                "tx_channels": self.device_configuration.get_param("tx_channels", [0, 1]),
                "rx_channels": self.device_configuration.get_param("rx_channels", [0, 1]),
                "if_freq": self.device_configuration.get_param("if_freq", [100e3, 110e3]),
            }
        }

    def _total_tx_rx(self):
        hw = self._hardware()
        n_tx = sum(len(v.get("tx_channels", [])) for v in hw.values())
        n_rx = sum(len(v.get("rx_channels", [])) for v in hw.values())
        return n_tx, n_rx

    def _load_from_config(self):
        channel_map = self.device_configuration.get_param("channel_map") or {}
        layout = channel_map.get("layout", "full_nxn")
        n_tx, n_rx = self._total_tx_rx()

        if layout == "hybrid_mimo":
            tx_global = channel_map.get("mimo", {}).get("tx_global", list(range(n_tx)))
            rx_global = channel_map.get("mimo", {}).get("rx_global", list(range(n_rx)))
        else:
            inject = {p["inject_tx"] for p in channel_map.get("dpic", [])}
            tx_global = [i for i in range(n_tx) if i not in inject]
            rx_global = list(range(n_rx))

        self._rebuild_matrix(tx_global, rx_global)

        for pair in channel_map.get("dpic", []):
            self._add_dpic_row(pair.get("inject_tx", 0), pair.get("measure_tx", 0))

        if not channel_map.get("dpic"):
            self._update_preview()

    def _rebuild_matrix(self, tx_global, rx_global):
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
                cb.setChecked(True)
                cb.stateChanged.connect(self._emit_channel_map)
                self.matrix_layout.addWidget(cb, row + 1, col + 1)
                row_checks.append((t, r, cb))
            self._matrix_checks.append(row_checks)

        self._tx_global = tx_global
        self._rx_global = rx_global

    def _add_dpic_row(self, inject_tx=0, measure_tx=0):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        n_tx, _ = self._total_tx_rx()
        inject_cb = QComboBox()
        measure_cb = QComboBox()
        for i in range(n_tx):
            inject_cb.addItem(f"Tx{i + 1} (inject)", i)
            measure_cb.addItem(f"Tx{i + 1}", i)
        inject_cb.setCurrentIndex(min(inject_tx, inject_cb.count() - 1))
        measure_cb.setCurrentIndex(min(measure_tx, measure_cb.count() - 1))

        remove_btn = QPushButton("Remove")
        row_layout.addWidget(QLabel("Inject:"))
        row_layout.addWidget(inject_cb)
        row_layout.addWidget(QLabel("Measure:"))
        row_layout.addWidget(measure_cb)
        row_layout.addWidget(remove_btn)

        self.dpic_container.addWidget(row_widget)
        entry = {
            "widget": row_widget,
            "inject": inject_cb,
            "measure": measure_cb,
        }
        self._dpic_rows.append(entry)

        inject_cb.currentIndexChanged.connect(self._emit_channel_map)
        measure_cb.currentIndexChanged.connect(self._emit_channel_map)
        remove_btn.clicked.connect(lambda: self._remove_dpic_row(entry))
        self._emit_channel_map()

    def _remove_dpic_row(self, entry):
        if entry in self._dpic_rows:
            self._dpic_rows.remove(entry)
            entry["widget"].deleteLater()
            self._emit_channel_map()

    def _build_channel_map(self) -> dict:
        tx_global = list(self._tx_global)
        rx_global = list(self._rx_global)
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
                "dpic": [
                    {
                        "inject_tx": row["inject"].currentData(),
                        "measure_tx": row["measure"].currentData(),
                    }
                    for row in self._dpic_rows
                ],
            }
        else:
            layout = "custom"
            channel_map = {
                "layout": layout,
                "pairs": pairs,
                "dpic": [
                    {
                        "inject_tx": row["inject"].currentData(),
                        "measure_tx": row["measure"].currentData(),
                    }
                    for row in self._dpic_rows
                ],
            }
        return channel_map

    def _update_preview(self):
        channel_map = self._build_channel_map()
        group_id = self.device_configuration.get_param("device_name") or "USRP"
        sources, _, dpic = resolve_channel_map(
            group_id, channel_map, self._hardware()
        )
        labels = sorted([s.label for s in sources])
        dpic_txt = ", ".join(
            f"Inject Tx{p.inject_tx + 1}→Measure Tx{p.measure_tx + 1}"
            for p in dpic
        )
        self.preview_label.setText(
            f"Sources ({len(labels)}): {', '.join(labels)}"
            + (f"\nDPIC: {dpic_txt}" if dpic_txt else "")
        )

    def _emit_channel_map(self):
        if self._locked:
            return
        channel_map = self._build_channel_map()
        self._update_preview()
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
            entry["widget"].findChildren(QPushButton)[0].setEnabled(not locked)

    def get_channel_map(self) -> dict:
        return self._build_channel_map()

"""Shared helpers for settings panel layout and hardware-aware params."""

from __future__ import annotations

from typing import Callable, Dict, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from bioview_common.datatypes.configuration.hardware_params import (
    GLOBAL_RX_PARAMS,
    GLOBAL_TX_PARAMS,
    resolve_param_values,
    update_device_rx_param,
    update_device_tx_param,
)

DEFAULT_MAX_PANEL_HEIGHT = 260

ParamSpec = Tuple[str, Tuple[float, float], float, float, int]


def wrap_scrollable(
    panel: QWidget, max_height: int = DEFAULT_MAX_PANEL_HEIGHT
) -> QScrollArea:
    """Wrap a settings panel so tall content scrolls instead of stretching the UI."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(panel)
    scroll.setMaximumHeight(max_height)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return scroll


def add_param_rows(
    grid: QGridLayout,
    device_configuration,
    param_mappings: Dict[str, Tuple[str, ParamSpec]],
    on_change: Callable[[str, object, int | None], None],
    start_row: int = 0,
) -> Tuple[dict, int]:
    """Add labeled spinbox row(s) per parameter; returns widgets dict and next row index."""
    param_inputs = {}
    row = start_row

    for param_name, (label_text, (min_val, max_val), multiplier, step, decimals) in (
        param_mappings.items()
    ):
        grid.addWidget(QLabel(label_text), row, 0)
        values = resolve_param_values(device_configuration, param_name)
        if not values:
            values = [min_val * multiplier if multiplier != 1 else min_val]

        input_widgets = []
        for col, value in enumerate(values):
            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(min_val), int(max_val))
                widget.setSingleStep(int(step))
                display_value = int(value) if isinstance(value, (int, float)) else int(min_val)
                widget.setValue(display_value)

                def _spin_changed(val, param_name=param_name, col=col, mult=multiplier):
                    stored = float(val) * mult if mult != 1 else float(val)
                    on_change(param_name, stored, col if len(values) > 1 else None)

                widget.valueChanged.connect(_spin_changed)
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(min_val), float(max_val))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))
                display_value = (
                    value / multiplier if isinstance(value, (int, float)) else float(min_val)
                )
                widget.setValue(float(display_value))

                def _dbl_changed(val, param_name=param_name, col=col, mult=multiplier):
                    stored = float(val) * mult if mult != 1 else float(val)
                    on_change(param_name, stored, col if len(values) > 1 else None)

                widget.valueChanged.connect(_dbl_changed)

            grid.addWidget(widget, row, col + 1)
            input_widgets.append(widget)

        param_inputs[param_name] = input_widgets
        row += 1

    return param_inputs, row


def hardware_aware_update_param(device_configuration, param: str, value, idx=None):
    """Update config object using nested hardware when present."""
    if param in GLOBAL_TX_PARAMS:
        return update_device_tx_param(device_configuration, param, value, idx)
    if param in GLOBAL_RX_PARAMS:
        return update_device_rx_param(device_configuration, param, value, idx)
    if idx is not None:
        current_value = resolve_param_values(device_configuration, param)
        updated_value = list(current_value)
        updated_value[idx] = value
    else:
        updated_value = value
    device_configuration.set_param(param, updated_value)
    return updated_value

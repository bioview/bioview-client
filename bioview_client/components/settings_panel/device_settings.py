from bioview_common import DeviceType
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .panel_utils import add_param_rows, hardware_aware_update_param
from .usrp_channel_map_panel import USRPChannelMapPanel


class DeviceSettingsPanel(QGroupBox):
    update_device_param = pyqtSignal(str, object)
    device_param_changed = pyqtSignal(str, str, object)
    log_event = pyqtSignal(str, str)

    def __init__(self, device_configuration, parent=None):
        super().__init__(
            f"{device_configuration.get_param('device_name', 'Device')} Settings", parent
        )
        self.device_configuration = device_configuration
        self.device_name = device_configuration.get_param("device_name", "Device")
        self.cfg_id = None

    def get_emittable_signals(self):
        return {"update_device_param": self.update_device_param}

    def update_param(self, param, value, idx=None):
        try:
            updated_value = hardware_aware_update_param(
                self.device_configuration, param, value, idx
            )
            self.device_param_changed.emit(
                self.cfg_id or self.device_name, param, updated_value
            )
            self.log_event.emit(
                "debug",
                f"{self.device_name}: Updated {param} successfully",
            )
        except Exception:
            self.log_event.emit("error", f"{self.device_name}: Updating {param} failed")


class USRPSettingsPanel(DeviceSettingsPanel):
    run_dpic_balance = pyqtSignal(str)

    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self._streaming_locked = False
        self.init_ui()

    def init_ui(self):
        outer = QVBoxLayout()
        grid = QGridLayout()

        param_mappings = {
            "tx_gain": ("TX Gain (dB)", (0, 70), 1, 1, 0),
            "rx_gain": ("RX Gain (dB)", (0, 70), 1, 1, 0),
            "tx_amplitude": ("IF Amplitude", (0, 1), 1, 0.1, 2),
            "tx_phase": ("IF Phase (deg)", (-180, 180), 1, 1, 1),
            "if_freq": ("IF Frequency (kHz)", (20, 400), 1e3, 0.1, 2),
            "samp_rate": ("Sample Rate (MSps)", (0.1, 10), 1e6, 0.1, 2),
            "carrier_freq": ("Carrier Freq. (MHz)", (30, 6000), 1e6, 1, 1),
        }

        self.param_inputs, row = add_param_rows(
            grid,
            self.device_configuration,
            param_mappings,
            self.update_param,
        )

        ctrl_row = QHBoxLayout()
        self.balance_button = QPushButton("Balance")
        self.balance_button.clicked.connect(self._on_balance_clicked)
        ctrl_row.addWidget(self.balance_button)

        cal_cfg = self.device_configuration.get_param("calibration") or {}
        self.calibration_checkbox = QCheckBox("Calibration signal")
        self.calibration_checkbox.setChecked(bool(cal_cfg.get("enabled", False)))
        self.calibration_checkbox.toggled.connect(self._on_calibration_toggled)
        ctrl_row.addWidget(self.calibration_checkbox)

        self.cal_shape_combo = QComboBox()
        self.cal_shape_combo.addItems(["triangle", "sawtooth", "rectangle"])
        shape = cal_cfg.get("shape", "triangle")
        shape_idx = self.cal_shape_combo.findText(shape)
        if shape_idx >= 0:
            self.cal_shape_combo.setCurrentIndex(shape_idx)
        self.cal_shape_combo.currentTextChanged.connect(self._on_cal_shape_changed)
        ctrl_row.addWidget(self.cal_shape_combo)
        ctrl_row.addStretch()
        grid.addLayout(ctrl_row, row, 0, 1, 4)

        outer.addLayout(grid)
        self.channel_map_panel = USRPChannelMapPanel(self.device_configuration)
        self.channel_map_panel.channel_map_changed.connect(self._on_channel_map_changed)
        outer.addWidget(self.channel_map_panel)
        self.setLayout(outer)

    def _on_balance_clicked(self):
        self.run_dpic_balance.emit(self.cfg_id or self.device_name)

    def _on_calibration_toggled(self, checked):
        cal = dict(self.device_configuration.get_param("calibration") or {})
        cal["enabled"] = checked
        self.update_param("calibration", cal)

    def _on_cal_shape_changed(self, shape):
        cal = dict(self.device_configuration.get_param("calibration") or {})
        cal["shape"] = shape
        self.update_param("calibration", cal)

    def _on_channel_map_changed(self, channel_map):
        self.update_param("channel_map", channel_map)

    def set_streaming_locked(self, locked: bool):
        self._streaming_locked = locked
        self.calibration_checkbox.setEnabled(not locked)
        self.cal_shape_combo.setEnabled(not locked)
        self.channel_map_panel.set_streaming_locked(locked)

    def get_emittable_signals(self):
        return {
            "update_device_param": self.update_device_param,
            "run_dpic_balance": self.run_dpic_balance,
        }


class BIOPACSettingsPanel(DeviceSettingsPanel):
    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)


class DummySettingsPanel(DeviceSettingsPanel):
    run_dpic_balance = pyqtSignal(str)

    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self._streaming_locked = False
        self._rf_mode = bool(device_configuration.get_param("hardware"))
        self.init_ui()

    def init_ui(self):
        if self._rf_mode:
            self._init_rf_ui()
        else:
            self._init_legacy_ui()

    def _init_legacy_ui(self):
        from PyQt6.QtWidgets import QDoubleSpinBox, QLabel

        layout = QGridLayout()
        self.param_inputs = {}
        param_specs = [
            ("samp_rate", "Sample Rate (Hz)", (1, 1000000), 100, 0),
            ("num_channels", "Channels", (1, 64), 1, 0),
            ("signal_freq", "Signal Freq. (Hz)", (0.01, 10000.0), 0.1, 2),
            ("amplitude", "Amplitude", (0.0, 1000.0), 0.1, 2),
            ("noise_std", "Noise Std-Dev", (0.0, 100.0), 0.1, 2),
            ("chunk_duration", "Chunk Duration (s)", (0.001, 1.0), 0.01, 3),
        ]
        for row, (param_name, label_text, (min_val, max_val), step, decimals) in enumerate(
            param_specs
        ):
            layout.addWidget(QLabel(label_text), row, 0)
            value = self.device_configuration.get_param(param_name)
            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(min_val), int(max_val))
                widget.setSingleStep(int(step))
                widget.setValue(
                    int(value) if isinstance(value, (int, float)) else int(min_val)
                )
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(min_val), float(max_val))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))
                widget.setValue(
                    float(value) if isinstance(value, (int, float)) else float(min_val)
                )
            widget.valueChanged.connect(
                lambda val, param_name=param_name: self.update_param(param_name, val)
            )
            layout.addWidget(widget, row, 1)
            self.param_inputs[param_name] = widget
        self.setLayout(layout)

    def _init_rf_ui(self):
        outer = QVBoxLayout()
        grid = QGridLayout()

        param_mappings = {
            "tx_gain": ("TX Gain (dB)", (0, 70), 1, 1, 0),
            "tx_amplitude": ("IF Amplitude", (0, 1), 1, 0.1, 2),
            "tx_phase": ("IF Phase (deg)", (-180, 180), 1, 1, 1),
            "if_freq": ("IF Frequency (kHz)", (20, 400), 1e3, 0.1, 2),
            "samp_rate": ("Sample Rate (MSps)", (0.1, 10), 1e6, 0.1, 2),
        }

        self.param_inputs, row = add_param_rows(
            grid,
            self.device_configuration,
            param_mappings,
            self.update_param,
        )

        ctrl_row = QHBoxLayout()
        self.balance_button = QPushButton("Balance")
        self.balance_button.clicked.connect(self._on_balance_clicked)
        ctrl_row.addWidget(self.balance_button)

        cal_cfg = self.device_configuration.get_param("calibration") or {}
        self.calibration_checkbox = QCheckBox("Calibration signal")
        self.calibration_checkbox.setChecked(bool(cal_cfg.get("enabled", False)))
        self.calibration_checkbox.toggled.connect(self._on_calibration_toggled)
        ctrl_row.addWidget(self.calibration_checkbox)

        self.cal_shape_combo = QComboBox()
        self.cal_shape_combo.addItems(["triangle", "sawtooth", "rectangle"])
        shape = cal_cfg.get("shape", "triangle")
        shape_idx = self.cal_shape_combo.findText(shape)
        if shape_idx >= 0:
            self.cal_shape_combo.setCurrentIndex(shape_idx)
        self.cal_shape_combo.currentTextChanged.connect(self._on_cal_shape_changed)
        ctrl_row.addWidget(self.cal_shape_combo)
        ctrl_row.addStretch()
        grid.addLayout(ctrl_row, row, 0, 1, 4)

        outer.addLayout(grid)
        self.channel_map_panel = USRPChannelMapPanel(self.device_configuration)
        self.channel_map_panel.channel_map_changed.connect(self._on_channel_map_changed)
        outer.addWidget(self.channel_map_panel)
        self.setLayout(outer)

    def _on_balance_clicked(self):
        self.run_dpic_balance.emit(self.cfg_id or self.device_name)

    def _on_calibration_toggled(self, checked):
        cal = dict(self.device_configuration.get_param("calibration") or {})
        cal["enabled"] = checked
        self.update_param("calibration", cal)

    def _on_cal_shape_changed(self, shape):
        cal = dict(self.device_configuration.get_param("calibration") or {})
        cal["shape"] = shape
        self.update_param("calibration", cal)

    def _on_channel_map_changed(self, channel_map):
        self.update_param("channel_map", channel_map)

    def set_streaming_locked(self, locked: bool):
        self._streaming_locked = locked
        if not self._rf_mode:
            return
        self.calibration_checkbox.setEnabled(not locked)
        self.cal_shape_combo.setEnabled(not locked)
        self.channel_map_panel.set_streaming_locked(locked)

    def get_emittable_signals(self):
        signals = {"update_device_param": self.update_device_param}
        if self._rf_mode:
            signals["run_dpic_balance"] = self.run_dpic_balance
        return signals


settings_panel_generators = {
    DeviceType.USRP: USRPSettingsPanel,
    DeviceType.BIOPAC: BIOPACSettingsPanel,
    DeviceType.DUMMY: DummySettingsPanel,
}


def get_device_settings_panel(device_configuration):
    device_type = device_configuration.get_param("device_type")
    if device_type not in settings_panel_generators:
        return None
    return settings_panel_generators[device_type](device_configuration)

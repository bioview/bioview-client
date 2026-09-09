import contextlib

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .panel_utils import add_param_rows, hardware_aware_update_param
from .usrp_channel_map_panel import USRPChannelMapPanel


class DeviceSettingsPanel(QGroupBox):
    #: Parameter display metadata, filled in by the panels that show a grid.
    PARAM_MAPPINGS: dict = {}

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

    def set_live_param(self, param, idx, value):
        """Reflect a value the *server* changed, without echoing it back.

        Signals are blocked around the write: letting the spin box emit would
        send an UPDATE_RUNNING_PARAMETER back to the server for a value the
        server itself just set, which during a balance means the UI fighting
        the search point by point. The configuration is updated alongside, so
        a later manual edit starts from what the hardware actually has.
        """
        widgets = self.param_inputs.get(param) or []
        if not isinstance(widgets, list) or idx is None or not 0 <= idx < len(widgets):
            return
        multiplier = self.PARAM_MAPPINGS.get(param, (None, None, 1))[2]
        widget = widgets[idx]
        display = float(value) / multiplier if multiplier != 1 else float(value)

        was_blocked = widget.blockSignals(True)
        try:
            widget.setValue(
                int(round(display)) if isinstance(widget, QSpinBox) else display
            )
        finally:
            widget.blockSignals(was_blocked)

        # A value the config cannot hold must not break the display of it.
        with contextlib.suppress(Exception):
            hardware_aware_update_param(self.device_configuration, param, value, idx)

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


class RFSettingsPanel(DeviceSettingsPanel):
    """Shared RF device tab: parameter grid, calibration row, channel map.

    The USRP panel and the dummy backend's RF panel differ only in which
    parameters they expose, so the layout and every calibration/channel-map
    handler live here -- they were duplicated line for line and drifted apart.
    """

    run_dpic_balance = pyqtSignal(str)

    PARAM_INPUT_WIDTH = 85

    def _build_rf_ui(self, param_mappings):
        # Side by side, not stacked: the settings tabs span the full window
        # width but are only a few rows tall, so a stacked channel map would sit
        # below the fold and the DPIC loops would need scrolling to find.
        outer = QHBoxLayout(self)
        outer.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(4)
        grid = QGridLayout()
        grid.setSpacing(4)

        self.param_inputs, row = add_param_rows(
            grid,
            self.device_configuration,
            param_mappings,
            self.update_param,
        )
        for widgets in self.param_inputs.values():
            for widget in widgets:
                widget.setMaximumWidth(self.PARAM_INPUT_WIDTH)

        left.addLayout(grid)
        left.addStretch(1)
        outer.addLayout(left)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(divider)

        # Balance and the calibration overlay sit above the channel map rather
        # than under the parameter grid: they act on the DPIC loops shown here,
        # and a USRP's seven parameter rows would push them off the tab.
        right = QVBoxLayout()
        right.setSpacing(4)
        right.addLayout(self._calibration_rows())

        self.channel_map_panel = USRPChannelMapPanel(self.device_configuration)
        self.channel_map_panel.channel_map_changed.connect(self._on_channel_map_changed)
        right.addWidget(self.channel_map_panel)
        right.addStretch(1)
        outer.addLayout(right, 1)

    #: Burst timing controls, as
    #: calibration key -> (label, tooltip, (min, max), step, decimals,
    #: display multiplier). The multiplier is what the config value is divided
    #: by for display, so a pulse stored in seconds is edited in milliseconds.
    CAL_TIMING_FIELDS = {
        "num_pulses": (
            "Pulses",
            "How many pulses each calibration burst contains.",
            (1, 1000),
            1,
            0,
            1.0,
        ),
        "pulse_duration_s": (
            "Pulse (ms)",
            "Duration of one calibration pulse.\n"
            "This is the shape's period, so 100 ms is a 10 Hz triangle.",
            (0.01, 10000.0),
            1.0,
            2,
            1e-3,
        ),
        "packet_spacing_s": (
            "Gap (s)",
            "How often the burst repeats: the time from the start of one\n"
            "burst to the start of the next. The burst itself lasts\n"
            "pulses x pulse duration; the rest of this interval is silence.",
            (0.001, 3600.0),
            0.1,
            3,
            1.0,
        ),
    }

    def _calibration_rows(self):
        """The calibration controls: what the pilot looks like, then its timing.

        Two rows rather than one: the burst timings are three more spin boxes,
        and a single row of eight controls wraps into the channel map on any
        window narrower than the rig it was laid out on.
        """
        rows = QVBoxLayout()
        rows.setSpacing(4)
        rows.addLayout(self._calibration_row())
        rows.addLayout(self._calibration_timing_row())
        return rows

    def _calibration_timing_row(self):
        """Number of pulses, pulse duration and burst repeat interval.

        All three are live: the right burst timing depends on what is being
        measured, and it used to be reachable only by editing the config file
        and restarting the session.
        """
        row = QHBoxLayout()
        row.setSpacing(4)
        cal_cfg = self.device_configuration.get_param("calibration") or {}
        self.cal_timing_inputs = {}

        for key, (
            label,
            tooltip,
            (minimum, maximum),
            step,
            decimals,
            multiplier,
        ) in self.CAL_TIMING_FIELDS.items():
            row.addWidget(QLabel(f"{label}:"))
            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(minimum), int(maximum))
                widget.setSingleStep(int(step))
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(minimum), float(maximum))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))

            widget.setToolTip(tooltip)
            widget.setMaximumWidth(self.PARAM_INPUT_WIDTH)
            value = cal_cfg.get(key)
            if value is None:
                value = minimum
            display = float(value) / multiplier
            widget.setValue(
                int(round(display)) if decimals == 0 else max(float(minimum), display)
            )
            widget.valueChanged.connect(
                lambda val, key=key, mult=multiplier, dec=decimals: (
                    self._update_calibration(
                        key, int(val) if dec == 0 else float(val) * mult
                    )
                )
            )
            self.cal_timing_inputs[key] = widget
            row.addWidget(widget)

        row.addStretch()
        return row

    def _calibration_row(self):
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)
        self.balance_button = QPushButton("Balance")
        self.balance_button.setToolTip(
            "Run the DPIC balance search for this device's cancellation loops"
        )
        self.balance_button.clicked.connect(self._on_balance_clicked)
        ctrl_row.addWidget(self.balance_button)

        cal_cfg = self.device_configuration.get_param("calibration") or {}
        self.calibration_checkbox = QCheckBox("Calibration signal")
        self.calibration_checkbox.setChecked(bool(cal_cfg.get("enabled", False)))
        self.calibration_checkbox.toggled.connect(self._on_calibration_toggled)
        ctrl_row.addWidget(self.calibration_checkbox)

        self.cal_shape_combo = QComboBox()
        self.cal_shape_combo.addItems(["triangle", "sawtooth", "rectangle"])
        shape_idx = self.cal_shape_combo.findText(cal_cfg.get("shape", "triangle"))
        if shape_idx >= 0:
            self.cal_shape_combo.setCurrentIndex(shape_idx)
        self.cal_shape_combo.currentTextChanged.connect(self._on_cal_shape_changed)
        ctrl_row.addWidget(self.cal_shape_combo)

        # Pilot amplitude as a fraction of the Tx carrier, adjustable live: the
        # right level depends on the coupling in the rig, not on the config.
        ctrl_row.addWidget(QLabel("Cal amp:"))
        self.cal_depth_spin = QDoubleSpinBox()
        self.cal_depth_spin.setRange(0.0, 1.0)
        self.cal_depth_spin.setSingleStep(0.01)
        self.cal_depth_spin.setDecimals(3)
        self.cal_depth_spin.setMaximumWidth(self.PARAM_INPUT_WIDTH)
        self.cal_depth_spin.setToolTip(
            "Calibration pilot amplitude relative to the Tx signal.\n"
            "1.0 is 100% modulation; 0 disables the overlay."
        )
        self.cal_depth_spin.setValue(float(cal_cfg.get("modulation_depth", 0.2)))
        self.cal_depth_spin.valueChanged.connect(self._on_cal_depth_changed)
        ctrl_row.addWidget(self.cal_depth_spin)
        ctrl_row.addStretch()
        return ctrl_row

    #: Fields a balance progress report maps onto, as
    #: report key -> (parameter, the report key naming the channel index).
    BALANCE_LIVE_FIELDS = {
        "phase_deg": ("tx_phase", "inject_tx"),
        "amplitude": ("tx_amplitude", "inject_tx"),
        "tx_gain_db": ("tx_gain", "measure_tx"),
        "rx_gain_db": ("rx_gain", "measure_rx"),
    }

    def apply_balance_progress(self, progress: dict):
        """Show what the running balance is doing to this device.

        The search drives phase, amplitude and both analog gains for a minute
        or more. Without this the panel sits on the values from before the
        balance started, so there is no way to tell a search that is working
        from one that is doing nothing at all.
        """
        for key, (param, index_key) in self.BALANCE_LIVE_FIELDS.items():
            value = progress.get(key)
            index = progress.get(index_key)
            if value is None or index is None:
                continue
            self.set_live_param(param, int(index), float(value))

        stage = progress.get("stage")
        point, planned = progress.get("point"), progress.get("planned")
        if stage and point and planned:
            self.balance_button.setText(f"{stage} {point}/{planned}")
        elif stage:
            self.balance_button.setText(str(stage))

    def _on_balance_clicked(self):
        # Disabled optimistically: the client answers asynchronously now, so
        # without this the button stays live and a second click queues a
        # balance the server will reject.
        self.set_balance_running(True)
        self.run_dpic_balance.emit(self.cfg_id or self.device_name)

    def set_balance_running(self, running: bool):
        """Reflect a balance in flight; the search takes a minute or more."""
        self.balance_button.setEnabled(not running)
        self.balance_button.setText("Balancing..." if running else "Balance")

    def _update_calibration(self, key, value):
        cal = dict(self.device_configuration.get_param("calibration") or {})
        cal[key] = value
        self.update_param("calibration", cal)

    def _on_calibration_toggled(self, checked):
        self._update_calibration("enabled", checked)

    def _on_cal_shape_changed(self, shape):
        self._update_calibration("shape", shape)

    def _on_cal_depth_changed(self, depth):
        self._update_calibration("modulation_depth", float(depth))

    def _on_channel_map_changed(self, channel_map):
        self.update_param("channel_map", channel_map)

    def set_streaming_locked(self, locked: bool):
        self._streaming_locked = locked
        # The calibration overlay is toggleable while live; only the channel
        # map, which changes the emitted row count, is frozen.
        self.channel_map_panel.set_streaming_locked(locked)

    def get_emittable_signals(self):
        return {
            "update_device_param": self.update_device_param,
            "run_dpic_balance": self.run_dpic_balance,
        }


class USRPSettingsPanel(RFSettingsPanel):
    PARAM_MAPPINGS = {
        "tx_gain": ("TX Gain (dB)", (0, 70), 1, 1, 0),
        "rx_gain": ("RX Gain (dB)", (0, 70), 1, 1, 0),
        "tx_amplitude": ("IF Amplitude", (0, 1), 1, 0.1, 2),
        "tx_phase": ("IF Phase (deg)", (-180, 180), 1, 1, 1),
        "if_freq": ("IF Frequency (kHz)", (20, 400), 1e3, 0.1, 2),
        "samp_rate": ("Sample Rate (MSps)", (0.1, 10), 1e6, 0.1, 2),
        "carrier_freq": ("Carrier Freq. (MHz)", (30, 6000), 1e6, 1, 1),
    }

    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self._streaming_locked = False
        self.init_ui()

    def init_ui(self):
        self._build_rf_ui(self.PARAM_MAPPINGS)


class BIOPACSettingsPanel(DeviceSettingsPanel):
    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self._streaming_locked = False
        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()
        self.param_inputs = {}
        param_specs = [
            ("samp_rate", "Sample Rate (Hz)", (1, 10000), 10, 0),
            ("connection_type", "Connection Type", (10, 20), 10, 0),
        ]
        for row, (
            param_name,
            label_text,
            (min_val, max_val),
            step,
            decimals,
        ) in enumerate(param_specs):
            layout.addWidget(QLabel(label_text), row, 0)
            value = self.device_configuration.get_param(param_name)
            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(min_val), int(max_val))
                widget.setSingleStep(int(step))
                widget.setValue(
                    int(value) if isinstance(value, int | float) else int(min_val)
                )
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(min_val), float(max_val))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))
                widget.setValue(
                    float(value) if isinstance(value, int | float) else float(min_val)
                )
            widget.valueChanged.connect(
                lambda val, param_name=param_name: self.update_param(param_name, val)
            )
            layout.addWidget(widget, row, 1)
            self.param_inputs[param_name] = widget

        channels = self.device_configuration.get_param("channels") or [1, 1, 1, 1]
        self.channel_checks = []
        ch_row = len(param_specs)
        layout.addWidget(QLabel("Enabled Channels"), ch_row, 0)
        ch_box = QHBoxLayout()
        for idx, enabled in enumerate(channels):
            cb = QCheckBox(f"Ch{idx + 1}")
            cb.setChecked(bool(enabled))
            cb.toggled.connect(self._on_channel_toggled)
            ch_box.addWidget(cb)
            self.channel_checks.append(cb)
        layout.addLayout(ch_box, ch_row, 1)
        self.setLayout(layout)

    def _on_channel_toggled(self, _checked: bool):
        channels = [1 if cb.isChecked() else 0 for cb in self.channel_checks]
        self.update_param("channels", channels)

    def set_streaming_locked(self, locked: bool):
        self._streaming_locked = locked
        for widget in getattr(self, "param_inputs", {}).values():
            widget.setEnabled(not locked)
        for cb in getattr(self, "channel_checks", []):
            cb.setEnabled(not locked)


class DummySettingsPanel(RFSettingsPanel):
    """Dummy backend: a plain signal generator, or the RF simulator.

    ``hardware`` in the config is what distinguishes them -- with it the dummy
    stands in for a USRP group and gets the same RF tab.
    """

    PARAM_MAPPINGS = {
        "tx_gain": ("TX Gain (dB)", (0, 70), 1, 1, 0),
        "tx_amplitude": ("IF Amplitude", (0, 1), 1, 0.1, 2),
        "tx_phase": ("IF Phase (deg)", (-180, 180), 1, 1, 1),
        "if_freq": ("IF Frequency (kHz)", (20, 400), 1e3, 0.1, 2),
        "samp_rate": ("Sample Rate (MSps)", (0.1, 10), 1e6, 0.1, 2),
    }

    LEGACY_PARAM_SPECS = [
        ("samp_rate", "Sample Rate (Hz)", (1, 1000000), 100, 0),
        ("num_channels", "Channels", (1, 64), 1, 0),
        ("signal_freq", "Signal Freq. (Hz)", (0.01, 10000.0), 0.1, 2),
        ("amplitude", "Amplitude", (0.0, 1000.0), 0.1, 2),
        ("noise_std", "Noise Std-Dev", (0.0, 100.0), 0.1, 2),
        ("chunk_duration", "Chunk Duration (s)", (0.001, 1.0), 0.01, 3),
    ]

    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self._streaming_locked = False
        self._rf_mode = bool(device_configuration.get_param("hardware"))
        self.init_ui()

    def init_ui(self):
        if self._rf_mode:
            self._build_rf_ui(self.PARAM_MAPPINGS)
        else:
            self._init_legacy_ui()

    def _init_legacy_ui(self):
        layout = QGridLayout()
        self.param_inputs = {}
        for row, (
            param_name,
            label_text,
            (min_val, max_val),
            step,
            decimals,
        ) in enumerate(self.LEGACY_PARAM_SPECS):
            layout.addWidget(QLabel(label_text), row, 0)
            value = self.device_configuration.get_param(param_name)
            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(min_val), int(max_val))
                widget.setSingleStep(int(step))
                widget.setValue(
                    int(value) if isinstance(value, int | float) else int(min_val)
                )
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(min_val), float(max_val))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))
                widget.setValue(
                    float(value) if isinstance(value, int | float) else float(min_val)
                )
            widget.setMaximumWidth(self.PARAM_INPUT_WIDTH)
            widget.valueChanged.connect(
                lambda val, param_name=param_name: self.update_param(param_name, val)
            )
            layout.addWidget(widget, row, 1)
            self.param_inputs[param_name] = widget
        layout.setColumnStretch(2, 1)
        self.setLayout(layout)

    def set_streaming_locked(self, locked: bool):
        self._streaming_locked = locked
        if not self._rf_mode:
            return
        super().set_streaming_locked(locked)

    def get_emittable_signals(self):
        if self._rf_mode:
            return super().get_emittable_signals()
        return {"update_device_param": self.update_device_param}


class MicrophoneSettingsPanel(DeviceSettingsPanel):
    """Host audio input: which device, how fast, how many channels, how loud.

    ``samp_rate`` is the recorded rate as well as the plotted one -- saving is
    fed from the display stream -- so it is the one setting here worth thinking
    about before a session rather than during it.
    """

    #: (label, (min, max), step, decimals). ``channels`` is a count here, not
    #: the per-channel enable mask BIOPAC uses: a sound card's inputs are not
    #: individually selectable.
    PARAM_SPECS = [
        ("samp_rate", "Sample Rate (Hz)", (1000, 96000), 1000, 0),
        ("channels", "Channels", (1, 8), 1, 0),
        ("gain", "Gain", (0.1, 100.0), 0.5, 2),
    ]

    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self._streaming_locked = False
        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()
        self.param_inputs = {}

        row = 0
        layout.addWidget(QLabel("Input Device"), row, 0)
        self.device_label = QLabel(
            str(self.device_configuration.get_param("device", "default") or "default")
        )
        # Read-only: the input is chosen in the configuration file and opening a
        # different one means tearing down and reopening the PortAudio stream,
        # which is not something to do from a spin box mid-session.
        self.device_label.setWordWrap(True)
        layout.addWidget(self.device_label, row, 1)

        for offset, (
            param_name,
            label_text,
            (min_val, max_val),
            step,
            decimals,
        ) in enumerate(self.PARAM_SPECS):
            row = offset + 1
            layout.addWidget(QLabel(label_text), row, 0)
            value = self.device_configuration.get_param(param_name)
            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(min_val), int(max_val))
                widget.setSingleStep(int(step))
                widget.setValue(
                    int(value) if isinstance(value, int | float) else int(min_val)
                )
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(min_val), float(max_val))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))
                widget.setValue(
                    float(value) if isinstance(value, int | float) else float(min_val)
                )
            widget.valueChanged.connect(
                lambda val, param_name=param_name: self.update_param(param_name, val)
            )
            layout.addWidget(widget, row, 1)
            self.param_inputs[param_name] = widget

        layout.setColumnStretch(2, 1)
        self.setLayout(layout)

    def set_streaming_locked(self, locked: bool):
        self._streaming_locked = locked
        for param, widget in getattr(self, "param_inputs", {}).items():
            # Gain is applied per chunk on the way out, so it is the one control
            # that is safe -- and useful -- to move while a session is running.
            widget.setEnabled(not locked or param == "gain")

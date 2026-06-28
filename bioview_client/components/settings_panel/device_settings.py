from bioview_common import Configuration, DeviceType
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDoubleSpinBox, QGridLayout, QGroupBox, QLabel, QSpinBox


class DeviceSettingsPanel(QGroupBox):
    update_device_param = pyqtSignal(str, object)
    # (configuration_id, param, value) emitted after a successful UI tweak so the
    # change can be recorded into the active recording's metadata
    device_param_changed = pyqtSignal(str, str, object)
    log_event = pyqtSignal(str, str)

    def __init__(self, device_configuration: Configuration, parent=None):
        super().__init__(
            f"{device_configuration.get_param("device_name", "Device")} Settings", parent
        )
        self.device_configuration = device_configuration
        self.device_name = device_configuration.get_param("device_name", "Device")
        # Set by SettingsPanel so emitted changes carry the configuration id
        self.cfg_id = None

    def get_emittable_signals(self):
        return {
            "update_device_param": self.update_device_param
        }
    
    # Parameter updates should be taken care of by the Configuration object since it is shared
    def update_param(self, param, value, idx=None):
        # We may have lists here, these need to be handled correctly
        if idx is not None:
            # Get value, update idx from list and emit overall
            current_value = self.device_configuration.get_param(param)
            updated_value = current_value
            updated_value[idx] = value
        else:
            updated_value = value

        try:
            self.device_configuration.set_param(param, updated_value)
            # TODO: Communicate with backend

            self.device_param_changed.emit(
                self.cfg_id or self.device_name, param, updated_value
            )
            self.log_event.emit(
                "debug",
                f"{self.device_name}: Updated {param} to {value} successfully",
            )
        except Exception:
            self.log_event.emit("error", f"{self.device_name}: Updating {param} failed")


class USRPSettingsPanel(DeviceSettingsPanel):
    # TODO: We need to move extra functionality such as gain
    # balancing/frequency sweeping into this panel.
    # We may need to expose this in the Configuration backend
    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)

        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()
        self.param_inputs = {}

        # Create parameter inputs
        param_mappings = {
            "tx_gain": {
                "disp_str": "TX Gain (dB)",
                "range": (0, 70),
                "multiplier": 1,  # In case units need a multiplier during storage, such as for frequency
                "step": 1,
            },
            "rx_gain": {
                "disp_str": "RX Gain (dB)",
                "range": (0, 70),
                "multiplier": 1,
                "step": 1,
            },
            "tx_amplitude": {
                "disp_str": "IF Amplitude",
                "range": (0, 1),
                "multiplier": 1,
                "step": 0.1,
            },
            "if_freq": {
                "disp_str": "IF Frequency (kHz)",
                "range": (20, 400),
                "multiplier": 1e3,  # kHz to Hz
                "step": 0.1,
            },
            "samp_rate": {
                "disp_str": "Sample Rate (MSps)",
                "range": (0.1, 10),
                "multiplier": 1e6,  # MSps to sps
                "step": 0.1,
            },
            "carrier_freq": {
                "disp_str": "Carrier Freq. (MHz)",
                "range": (30, 6000),
                "multiplier": 1e6,  # MHz to Hz
                "step": 1,
            },
        }

        row = 0
        for param_name, val in param_mappings.items():
            label_text = val["disp_str"]
            min_val = val["range"][0]
            max_val = val["range"][1]
            multiplier = val["multiplier"]
            step = val["step"]

            # Add text for widget
            layout.addWidget(QLabel(label_text), row, 0)

            # Make as many spin boxes as channels specified
            current_values = self.device_configuration.get_param(param_name)
            values = (
                current_values
                if isinstance(current_values, (list, tuple))
                else [current_values]
            )

            input_widgets = []

            for col, value in enumerate(values):
                # A param may be missing from a partial config (value is None) or
                # otherwise non-numeric. Fall back to the range minimum so the spin
                # box still constructs instead of crashing on setValue(None).
                if isinstance(value, (int, float)):
                    display_value = value / multiplier
                else:
                    display_value = min_val

                # Make widget
                widget = QDoubleSpinBox()
                widget.setRange(min_val, max_val)
                widget.setDecimals(2)
                widget.setSingleStep(step)
                widget.setValue(display_value)

                # Connect signal
                idx = col if len(values) > 1 else None
                widget.valueChanged.connect(
                    lambda val, param_name=param_name, idx=idx: self.update_param(
                        param_name=param_name, value=val, idx=idx
                    )
                )

                layout.addWidget(widget, row, col + 1)
                input_widgets.append(widget)

            self.param_inputs[param_name] = input_widgets
            row += 1

        self.setLayout(layout)


class BIOPACSettingsPanel(DeviceSettingsPanel):
    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)


class DummySettingsPanel(DeviceSettingsPanel):
    """Settings for the virtual sine-wave device. Sampling rate and channel count
    are init-time parameters; signal frequency / amplitude / noise can be tweaked
    live for testing convenience."""

    def __init__(self, device_configuration, parent=None):
        super().__init__(device_configuration, parent)
        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()
        self.param_inputs = {}

        # (param, label, range, step, decimals); decimals == 0 -> integer spin box
        param_specs = [
            ("samp_rate", "Sample Rate (Hz)", (1, 1000000), 100, 0),
            ("num_channels", "Channels", (1, 64), 1, 0),
            ("signal_freq", "Signal Freq. (Hz)", (0.01, 10000.0), 0.1, 2),
            ("amplitude", "Amplitude", (0.0, 1000.0), 0.1, 2),
            ("noise_std", "Noise Std-Dev", (0.0, 100.0), 0.1, 2),
            ("chunk_duration", "Chunk Duration (s)", (0.001, 1.0), 0.01, 3),
        ]

        for row, (param_name, label_text, (min_val, max_val), step, decimals) in enumerate(param_specs):
            layout.addWidget(QLabel(label_text), row, 0)

            value = self.device_configuration.get_param(param_name)

            if decimals == 0:
                widget = QSpinBox()
                widget.setRange(int(min_val), int(max_val))
                widget.setSingleStep(int(step))
                widget.setValue(int(value) if isinstance(value, (int, float)) else int(min_val))
            else:
                widget = QDoubleSpinBox()
                widget.setRange(float(min_val), float(max_val))
                widget.setDecimals(decimals)
                widget.setSingleStep(float(step))
                widget.setValue(float(value) if isinstance(value, (int, float)) else float(min_val))

            widget.valueChanged.connect(
                lambda val, param_name=param_name: self.update_param(
                    param_name=param_name, value=val
                )
            )

            layout.addWidget(widget, row, 1)
            self.param_inputs[param_name] = widget

        self.setLayout(layout)


settings_panel_generators = {
    DeviceType.USRP: USRPSettingsPanel,
    DeviceType.BIOPAC: BIOPACSettingsPanel,
    DeviceType.DUMMY: DummySettingsPanel,
}


def get_device_settings_panel(device_configuration):
    device_type = device_configuration.get_param("device_type")
    if device_type not in settings_panel_generators:
        return

    return settings_panel_generators[device_type](device_configuration)

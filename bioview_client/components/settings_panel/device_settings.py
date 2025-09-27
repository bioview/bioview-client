from bioview_common import Configuration, DeviceType
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDoubleSpinBox, QGridLayout, QGroupBox, QLabel


class DeviceSettingsPanel(QGroupBox):
    update_device_param = pyqtSignal(str, object)
    log_event = pyqtSignal(str, str)

    def __init__(self, device_configuration: Configuration, parent=None):
        super().__init__(
            f"{device_configuration.get_param("device_name", "Device")} Settings", parent
        )
        self.device_configuration = device_configuration
        self.device_name = device_configuration.get_param("device_name", "Device")

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
                # Make current value an integer
                display_value = (
                    value / multiplier if isinstance(value, (int, float)) else value
                )

                # Make widget
                widget = QDoubleSpinBox()
                widget.setRange(min_val, max_val)
                widget.setDecimals(2)
                widget.setSingleStep(step)
                widget.setValue(display_value)

                # Connect signal
                idx = col if len(values) > 1 else None
                val = value * multiplier
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


settings_panel_generators = {
    DeviceType.USRP: USRPSettingsPanel,
    DeviceType.BIOPAC: BIOPACSettingsPanel,
}


def get_device_settings_panel(device_configuration):
    device_type = device_configuration.get_param("device_type")
    if device_type not in settings_panel_generators:
        return

    return settings_panel_generators[device_type](device_configuration)

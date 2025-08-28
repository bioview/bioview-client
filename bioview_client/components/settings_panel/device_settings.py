from bioview_common import Configuration
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
)


def get_list(x):
    return x if isinstance(x, (list, tuple)) else [x]


class DeviceSettingsPanel(QGroupBox):
    update_device_param = pyqtSignal(str, object)
    log_event = pyqtSignal(str, str)

    def __init__(self, device_configuration: Configuration, parent=None):
        super().__init__("Device Settings", parent)

        self.device_name = device_configuration.get_param("device_name", "dummy_device")
        self.device_configuration = device_configuration

        self.init_ui()

    def init_ui(self):
        layout = QGridLayout()

        # Create parameter inputs
        param_mappings = self.device_configuration.get_ui_params()

        row = 0
        for param, param_dict in param_mappings.items():
            label_text = param_dict["display_label"]
            # Get value as a list for convenience
            value = get_list(param_dict["value"])

            multiplier = param_dict["multiplier"]

            # (optional) load parameter range
            # (optional) specify parameter step
            range = param_dict.get("range", [])

            step = param_dict.get("step", 1)

            # Create widget, on the basis of specified type
            widget_type = param_dict["display_type"]

            # Add text for widget
            layout.addWidget(QLabel(label_text), row, 0)

            val_layout = QHBoxLayout()
            if widget_type == "text":
                # Make spin box(es)
                for idx, val in enumerate(value):
                    display_val = (
                        val / multiplier if isinstance(val, (int, float)) else val
                    )
                    widget = QDoubleSpinBox()
                    if range:
                        widget.setRange(range[0], range[1])
                    widget.setDecimals(2)
                    widget.setSingleStep(step)
                    widget.setValue(display_val)

                    # Add callback
                    widget.valueChanged.connect(
                        lambda val,
                        multiplier=multiplier,
                        param=param,
                        idx=idx: self.update_param(
                            param=param, value=multiplier * val, idx=idx
                        )
                    )

                    val_layout.addWidget(widget)
            elif widget_type == "slider":
                # Make slider(s)
                for idx, val in enumerate(value):
                    display_val = val
                    # Use appropriate operation depending on type
                    if isinstance(val, (int, float)):
                        display_val = (
                            val / multiplier
                            if isinstance(val, float)
                            else val // multiplier
                        )
                    widget = QSlider(Qt.Orientation.Horizontal, self)
                    if range:
                        widget.setMinimum(range[0])
                        widget.setMaximum(range[1])

                    widget.setTickPosition(QSlider.TickPosition.TicksBelow)
                    widget.setTickInterval(step)
                    widget.setValue(display_val)

                    # Add callback
                    widget.valueChanged.connect(
                        lambda val,
                        multiplier=multiplier,
                        param=param,
                        idx=idx: self.update_param(
                            param=param, value=multiplier * val, idx=idx
                        )
                    )

                    val_layout.addWidget(widget)
            else:
                return

            layout.addLayout(val_layout, row, 1)
            row += 1

        self.setLayout(layout)

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

            self.log_event.emit(
                "debug",
                f"{self.device_name}: Updated {param} to {value} successfully",
            )
        except Exception:
            self.log_event.emit("error", f"{self.device_name}: Updating {param} failed")


# TODO: We need to move extra functionality such as gain balancing/frequency sweeping into this panel.
# We may need to expose this in the Configuration backend

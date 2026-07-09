import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QGridLayout, QWidget

from bioview_common import DataSource
from bioview_client.constants import get_color_by_idx


class PlotManager:
    def __init__(
        self,
        config,
        color: str,
        display_duration: float,
        data_src: DataSource = None,
        xlabel: str = "Time (s)",
        ylabel: str = "Amplitude",
    ):
        # UI widget
        self.widget = pg.PlotWidget()
        self.widget.setAntialiasing(True)
        self.widget.getPlotItem().setDownsampling(auto=True, mode="peak")
        self.widget.enableAutoRange(pg.ViewBox.YAxis, enable=True)
        self.widget.enableAutoRange(pg.ViewBox.XAxis, enable=False)
        self.widget.setMouseEnabled(x=False, y=False)
        self.widget.setBackground(None)
        self.widget.showGrid(x=True, y=True)
        self.widget.setLabel("bottom", xlabel)
        self.widget.setLabel("left", ylabel)

        # Create pen and plot item ONCE - this is key for performance
        self.pen = pg.mkPen(color=color, width=1)
        self.plot_item = self.widget.plot([], [], pen=self.pen)

        # Plot specs
        self.config = config
        self.display_duration = display_duration

        # Data handling
        self.data_src = data_src

        # Dirty flag: only redraw when new data has arrived since the last frame
        self._dirty = False

        # Initialize after setting up basic properties
        self._init_plot()

    def _init_plot(self):
        # Number of points held on screen, sized by the (decimated) display rate.
        if self.data_src is None:
            disp_freq = 10.0
        else:
            disp_freq = self.data_src.get_disp_freq()
        self.num_points = max(2, int(self.display_duration * disp_freq))

        # Fixed-size numpy ring buffer (the sliding window) and reusable time axis
        self.buffer = np.zeros(self.num_points, dtype=float)
        self.time_vector = np.linspace(
            0, self.display_duration, self.num_points, endpoint=False
        )

        # Set initial data on the plot item (don't create a new plot)
        self.plot_item.setData(self.time_vector, self.buffer)
        self._dirty = False

        # Set ranges correctly
        self.widget.setXRange(0, self.display_duration, padding=0)

    def update_data_source(self, data_src: DataSource = None):
        self.widget.setTitle(str(data_src) if data_src is not None else "")
        self.data_src = data_src
        self._init_plot()

    def _decimate(self, arr: np.ndarray) -> np.ndarray:
        """Stride-decimate an incoming chunk so we never push more than one
        screen's worth of points per chunk. This keeps the displayed rate roughly
        independent of the (much higher) acquisition/save rate and bounds work."""
        n = arr.size
        if n > self.num_points:
            stride = int(np.ceil(n / self.num_points))
            arr = arr[::stride]
        return arr

    def add_data(self, data):
        """Append a chunk to the ring buffer using vectorized array ops (no
        per-sample Python loop)."""
        arr = np.asarray(data, dtype=float).ravel()
        if arr.size == 0:
            return

        arr = self._decimate(arr)
        n = arr.size

        if n >= self.num_points:
            # Chunk fills (or overfills) the window: keep the most recent points
            self.buffer[:] = arr[-self.num_points:]
        else:
            # Slide the window left by n and append the new samples at the end
            self.buffer[:-n] = self.buffer[n:]
            self.buffer[-n:] = arr

        self._dirty = True

    def update_plot(self):
        # Bounded work per tick: at most one setData using the existing ndarray
        if not self._dirty:
            return
        self.plot_item.setData(self.time_vector, self.buffer)
        self._dirty = False

    def update_display_duration(self, duration):
        self.display_duration = duration
        self._init_plot()


class PlotGrid(QWidget):
    log_event = pyqtSignal(str, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

        self.rows = 2
        self.cols = 2
        self.display_duration = 10.0

        self.selected_channels = {}

        # Set up the layout
        self.layout = QGridLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(5)

        # Keep track of available plots that are not connected to an output
        self.available_slots = []

        # Optimize refresh rate and ensure real-time performance
        self.refresh_time = max(self._get_monitor_refresh_delay(), 10)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_plots)

        # Initialize grid
        self.init_grid()
        self.update_timer.start(self.refresh_time)

    def _get_monitor_refresh_delay(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            return int(1000 // screen.refreshRate())
        else:
            return 16  # 60 Hz by default

    # Handle theme changes
    def event(self, event):
        if event.type() == QEvent.Type.PaletteChange:
            for r in range(self.rows):
                for c in range(self.cols):
                    self.plots[r][c].widget.setBackground(None)
                    self.plots[r][c].pen = pg.mkPen(
                        color=get_color_by_idx(r * self.cols + c), width=1
                    )
        return super().event(event)

    def init_grid(self):
        self.plots = [[None for _ in range(self.cols)] for _ in range(self.rows)]

        for r in range(self.rows):
            for c in range(self.cols):
                plot_obj = PlotManager(
                    config=self.config,
                    color=get_color_by_idx(r * self.cols + c),
                    display_duration=self.display_duration,
                )

                self.layout.addWidget(plot_obj.widget, r, c)
                self.plots[r][c] = plot_obj

                # Initially, all slots are available
                self.available_slots.append((r, c))

    def update_grid(self, rows, cols):
        """Resize the plot grid while keeping already-plotted sources in their
        same (row, col) cell when that cell still exists. Sources whose cell no
        longer fits the smaller grid are dropped and returned so the caller can
        keep the source selector in sync."""
        # Snapshot what is currently plotted and where
        old_locs = {src: info["loc"] for src, info in self.selected_channels.items()}

        # Clear past grid
        while self.layout.count():
            child = self.layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # Re-initialize
        self.rows = rows
        self.cols = cols
        self.selected_channels = {}

        # Flush queue of available slots
        self.available_slots = []

        self.init_grid()

        # Re-place sources at their original cell if it still exists; otherwise
        # they fall off the (smaller) grid and are reported as dropped.
        dropped = []
        for src, (r, c) in sorted(old_locs.items(), key=lambda kv: (kv[1][0], kv[1][1])):
            if r < self.rows and c < self.cols:
                self._assign_source(src, r, c)
            else:
                dropped.append(src)

        return dropped

    def _assign_source(self, source, row, col):
        """Bind a data source to a specific grid cell and mark the slot taken."""
        plot_obj = self.plots[row][col]
        plot_obj.update_data_source(source)
        self.selected_channels[source] = {"plot": plot_obj, "loc": (row, col)}
        if (row, col) in self.available_slots:
            self.available_slots.remove((row, col))

    def add_source(self, source):
        if source in self.selected_channels.keys():
            self.log_event.emit(
                "debug", "Unable to add channel as it is already being plotted"
            )
            return True

        if not self.available_slots:
            self.log_event.emit(
                "warning",
                "All graph slots full. Update layout or remove an existing trace.",
            )
            return False

        # Fill the lowest-index free slot (row-major)
        self.available_slots.sort(key=lambda x: x[0] * self.cols + x[1])
        row, col = self.available_slots[0]
        self._assign_source(source, row, col)

        return True

    def remove_source(self, channel):
        if channel not in self.selected_channels.keys():
            self.log_event.emit(
                "debug", "Unable to remove channel as it is not being plotted"
            )
            return

        # Clear the plot
        plot_obj = self.selected_channels[channel]["plot"]
        loc = self.selected_channels[channel]["loc"]

        plot_obj.update_data_source()

        # Remove from data structures
        self.selected_channels.pop(channel, None)
        self.available_slots.append(tuple(loc))

        return True

    def add_new_data(self, data, sources=None):
        """Route a (num_sources, num_samples) chunk to the selected plots using the
        per-chunk source list. Rows whose source is not currently plotted are
        ignored."""
        if data is None or sources is None:
            return

        data = np.atleast_2d(data)
        n_rows = data.shape[0]

        for idx in range(min(n_rows, len(sources))):
            src = sources[idx]
            source = DataSource.from_dict(src) if isinstance(src, dict) else src

            entry = self.selected_channels.get(source)
            if entry is None:
                continue

            entry["plot"].add_data(data[idx, :])

    def update_plots(self):
        for val in self.selected_channels.values():
            plot_obj = val["plot"]
            plot_obj.update_plot()

    def set_display_time(self, dur):
        self.display_duration = dur
        for r in range(self.rows):
            for c in range(self.cols):
                self.plots[r][c].update_display_duration(dur)

    def clear_sources(self):
        """Clear all current source-to-plot bindings and reset slot availability."""
        for entry in self.selected_channels.values():
            with np.errstate(all="ignore"):
                entry["plot"].update_data_source()
        self.selected_channels = {}
        self.available_slots = [(r, c) for r in range(self.rows) for c in range(self.cols)]

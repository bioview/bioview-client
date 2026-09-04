import numpy as np
import pyqtgraph as pg
from bioview_common import DataSource
from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QGridLayout, QWidget

from bioview_client.constants import get_color_by_idx


# (left, top, right, bottom) padding around each PlotItem, in pixels.
PLOT_MARGINS = (4, 4, 10, 6)
# Ticks plus one line of axis label.
BOTTOM_AXIS_HEIGHT = 46
LEFT_AXIS_WIDTH = 62


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
        plot_item = self.widget.getPlotItem()
        plot_item.setDownsampling(auto=True, mode="peak")
        self.widget.enableAutoRange(pg.ViewBox.YAxis, enable=True)
        self.widget.enableAutoRange(pg.ViewBox.XAxis, enable=False)
        self.widget.setMouseEnabled(x=False, y=False)
        self.widget.setBackground(None)
        self.widget.showGrid(x=True, y=True)
        self.widget.setLabel("bottom", xlabel)
        self.widget.setLabel("left", ylabel)

        # PlotItem lays its axes flush against the widget edge, so in a tight
        # grid the bottom axis label is clipped without these margins.
        plot_item.setContentsMargins(*PLOT_MARGINS)
        # pyqtgraph sizes an axis from its ticks and never grows it to fit
        # the label, so the extents are reserved explicitly.
        plot_item.getAxis("bottom").setHeight(BOTTOM_AXIS_HEIGHT)
        plot_item.getAxis("left").setWidth(LEFT_AXIS_WIDTH)

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
        disp_freq = 10.0 if self.data_src is None else self.data_src.get_disp_freq()
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
        # Same "Device: Source" name the plot-source selector shows.
        title = data_src.get_display_label() if data_src is not None else ""
        self.widget.setTitle(title)
        self.data_src = data_src
        self._init_plot()

    def _decimate(self, arr: np.ndarray) -> np.ndarray:
        """Stride-decimate a chunk to at most one screen's worth of points."""
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
            self.buffer[:] = arr[-self.num_points :]
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

    def set_color(self, color):
        """Re-pen the existing curve (used on a theme change)."""
        self.pen = pg.mkPen(color=color, width=1)
        self.plot_item.setPen(self.pen)


class PlotGrid(QWidget):
    log_event = pyqtSignal(str, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config

        self.rows = 2
        self.cols = 2
        self.display_duration = 10.0

        self.selected_channels = {}

        # descriptor dict -> DataSource, so routing does not rebuild one
        # object per row per chunk.
        self._source_cache = {}

        # Set up the layout
        self.layout = QGridLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(8)

        # Keep track of available plots that are not connected to an output
        self.available_slots = []

        # Optimize refresh rate and ensure real-time performance
        self.refresh_time = max(self._get_monitor_refresh_delay(), 10)
        # Parented so the timer dies with the widget; an unparented one keeps
        # firing against destroyed C++ objects.
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_plots)

        # Initialize grid
        self.init_grid()
        self.update_timer.start(self.refresh_time)

    def closeEvent(self, event):
        self.update_timer.stop()
        super().closeEvent(event)

    def _get_monitor_refresh_delay(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            return int(1000 // screen.refreshRate())
        else:
            return 16  # 60 Hz by default

    # Handle theme changes
    def event(self, event):
        # A PaletteChange can arrive before init_grid() has run.
        if event.type() == QEvent.Type.PaletteChange and getattr(self, "plots", None):
            for r in range(self.rows):
                for c in range(self.cols):
                    plot_obj = self.plots[r][c]
                    plot_obj.widget.setBackground(None)
                    # The pen has to be pushed onto the plot item, not just
                    # reassigned.
                    plot_obj.set_color(get_color_by_idx(r * self.cols + c))
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
        """Resize the grid, keeping plotted sources in their cell where it
        still exists. Sources that no longer fit are returned to the caller."""
        # Snapshot what is currently plotted and where
        old_locs = {src: info["loc"] for src, info in self.selected_channels.items()}

        # update_plots() must never run against a half-rebuilt grid.
        self.update_timer.stop()

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

        # Sources whose cell no longer exists are reported as dropped.
        dropped = []
        for src, (r, c) in sorted(old_locs.items(), key=lambda kv: (kv[1][0], kv[1][1])):
            if r < self.rows and c < self.cols:
                self._assign_source(src, r, c)
            else:
                dropped.append(src)

        self.update_timer.start(self.refresh_time)
        return dropped

    def _assign_source(self, source, row, col):
        """Bind a data source to a specific grid cell and mark the slot taken."""
        plot_obj = self.plots[row][col]
        plot_obj.update_data_source(source)
        self.selected_channels[source] = {"plot": plot_obj, "loc": (row, col)}
        if (row, col) in self.available_slots:
            self.available_slots.remove((row, col))

    def add_source(self, source):
        if source in self.selected_channels:
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
        if channel not in self.selected_channels:
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
        """Route a chunk to the selected plots using the per-chunk source list."""
        if data is None or sources is None:
            return

        data = np.atleast_2d(data)
        n_rows = data.shape[0]

        for idx in range(min(n_rows, len(sources))):
            src = sources[idx]
            if isinstance(src, dict):
                key = (src.get("group_id"), src.get("channel"))
                source = self._source_cache.get(key)
                if source is None:
                    source = DataSource.from_dict(src)
                    self._source_cache[key] = source
            else:
                source = src

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
        self.available_slots = [
            (r, c) for r in range(self.rows) for c in range(self.cols)
        ]

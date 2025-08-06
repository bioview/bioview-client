'''
BioView Monitor can be launched via CLI, with/without configuration JSONs pre-specified. They may also be launched using GUI without any specified configuration. 
In case no valid configuration files are found, the app will prompt the user to provide configuration JSONs.
Regardless on any configurations, the UI will load with appropriate components/default values
'''

import sys 
import logging # TODO: Remove 
from pathlib import Path

from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget, QDialog

from typing import List, Dict
from bioview_common import ConnectionStatus, RunningStatus, DataSource

from bioview_client.components import (
    AnnotateEventPanel,
    AppControlPanel,
    ConfigurationPrompt,
    ExperimentSettingsPanel,
    LogDisplayPanel,
    PlotGrid,
    StatusBar,
    TextDialog,
    UsrpDeviceConfigPanel
)
from bioview_client.handler import Client
from bioview_client.constants import DEFAULT_COMMON_CONFIGURATION

class BioViewMonitor(QMainWindow):
    def __init__(
        self,
        device_config: List[Dict] = None,
        common_config: Dict = None,
    ):
        super().__init__()
        
        # Check for valid configuration files provided and, if None, ask user to add 
        if common_config is None or device_config is None: 
            dialog = ConfigurationPrompt(self)
            configurations = None 

            if dialog.exec() == QDialog.DialogCode.Accepted:
                configurations = dialog.get_configurations()
            
            if configurations: 
                common_config = configurations['common']
                device_config = configurations['device']
            else: 
                # Generate a default common configuration
                common_config = DEFAULT_COMMON_CONFIGURATION

        # Store configurations 
        self.common_config = common_config

        self.devices = {}
        if device_config is not None and isinstance(device_config, dict):
            for device_id, device_cfg in device_config.items(): 
                self.devices[device_id] = {
                    'config': device_cfg,
                    'state': ConnectionStatus.DISCONNECTED
                }

        self.common_config['available_channels'] = []

        self.running_status = RunningStatus.NOINIT
        self.saving_status = False

        # Track instruction
        self.instruction_dialog = None
        if 'instruction_type' in self.common_config.keys():
            self.enable_instructions = False
            if self.common_config['instruction_type'] == 'text': 
                self.instruction_dialog = TextDialog()
        else:
            self.enable_instructions = True

        # Set up UI
        self.init_ui()
        self.setup_client()
        
        ### Common Threads
        self.instructions_thread = None

        ### Data Queues
        # self.disp_queue = queue.Queue(maxsize=10000)

        # Enable Logging
        self._connect_logging()
        # self._connect_display()
    
    def init_ui(self):
        # Define main wndow
        self.setWindowTitle("BioView Data Monitor")
        iconDir = (
            Path(__file__).resolve().parent.parent / "docs" / "assets" / "icon.png"
        )

        self.setWindowIcon(QIcon(str(iconDir)))
        screen = QGuiApplication.primaryScreen().geometry()
        width = screen.width()
        height = screen.height()
        self.setGeometry(
            int(0.2 * width), int(0.1 * height), int(0.6 * width), int(0.8 * height)
        )

        # Create central widget and main layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Top shelf container
        top_layout = QHBoxLayout()

        # All controls are in one container
        controls_layout = QVBoxLayout()

        # Connect/Start/Stop/Balance Signal Buttons
        self.app_control_panel = AppControlPanel()
        controls_layout.addWidget(self.app_control_panel, stretch=1)

        # Connect signal handlers
        self.app_control_panel.connectionInitiated.connect(self.handle_connection_requested)
        self.app_control_panel.startRequested.connect(self.handle_streaming_start_requested)
        self.app_control_panel.stopRequested.connect(self.handle_streaming_stop_requested)
        self.app_control_panel.saveRequested.connect(self.update_save_state)
        self.app_control_panel.instructionsEnabled.connect(self.toggle_instructions)
        # self.app_control_panel.balanceRequested.connect(self.perform_gain_balancing)
        # self.app_control_panel.sweepRequested.connect(self.perform_frequency_sweep)

        experiment_layout = QHBoxLayout()

        # Experiment Control Panel
        self.experiment_settings_panel = ExperimentSettingsPanel(self.common_config)
        experiment_layout.addWidget(self.experiment_settings_panel, stretch=1)
        # Connect handlers
        self.experiment_settings_panel.timeWindowChanged.connect(
            self.handle_time_window_change
        )
        self.experiment_settings_panel.gridLayoutChanged.connect(
            self.handle_grid_layout_change
        )
        self.experiment_settings_panel.addChannelRequested.connect(
            self.handle_add_source
        )
        self.experiment_settings_panel.removeChannelRequested.connect(
            self.handle_remove_source
        )

        # USRP Device Config Panel(s)
        usrp_cfg = []
        for device_dict in self.devices.values():
            if type(device_dict['config']).__name__ == 'MultiUsrpConfiguration': 
                usrp_cfg = device_dict["config"].get_individual_configs()

        self.usrp_config_panel = [None] * len(usrp_cfg)
        for idx, cfg in enumerate(usrp_cfg):
            self.usrp_config_panel[idx] = UsrpDeviceConfigPanel(cfg)
            experiment_layout.addWidget(self.usrp_config_panel[idx], stretch=1)

        controls_layout.addLayout(experiment_layout, stretch=1)
        top_layout.addLayout(controls_layout, stretch=3)

        # Metadata Panels
        self.meta_panels = QVBoxLayout()
        # Status Panel - Experiment Log goes here
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.log_display_panel = LogDisplayPanel(logger=self.logger)
        self.meta_panels.addWidget(self.log_display_panel, stretch=3)

        # Annotation Panel
        self.annotate_event_panel = AnnotateEventPanel(self.common_config)
        self.meta_panels.addWidget(self.annotate_event_panel, stretch=2)
        top_layout.addLayout(self.meta_panels, stretch=2)

        main_layout.addLayout(top_layout)

        # Plot Grid
        self.plot_grid = PlotGrid(self.common_config)
        main_layout.addWidget(self.plot_grid)

        central_widget.setLayout(main_layout)

        # Status Bar
        self.setStatusBar(StatusBar(self))

    def _connect_logging(self):
        self.plot_grid.logEvent.connect(self.log_display_panel.log_message)
        for _, panel in enumerate(self.usrp_config_panel):
            panel.logEvent.connect(self.log_display_panel.log_message)
        
    def setup_client(self):
        """Connect to client"""
        self.client_worker = Client()
        
        # Connect signals
        self.client_worker.server_connected.connect(self.on_server_connected)
        self.client_worker.server_disconnected.connect(self.on_server_disconnected)
        self.client_worker.error_occurred.connect(lambda msg: self.log_display_panel.log_message("error", msg))
        self.client_worker.log_message.connect(self.log_display_panel.log_message)
        
        # self.client_worker.streaming_started.connect(self.on_streaming_started)
        # self.client_worker.streaming_stopped.connect(self.on_streaming_stopped)
        self.client_worker.device_connected.connect(self.handle_device_connected)
        self.client_worker.device_connection_failed.connect(self.handle_device_connection_failed)
        self.client_worker.device_disconnected.connect(self.handle_device_disconnected)
    
        # Start client
        self.client_worker.start_client()
        
    def closeEvent(self, event):
        """Handle application close"""
        if self.client_worker:
            self.client_worker.stop_client()
        event.accept()
    
    # Handlers for UI updates
    def handle_time_window_change(self, seconds):
        self.plot_grid.set_display_time(seconds)

    def handle_grid_layout_change(self, rows, cols):
        self.plot_grid.update_grid(rows, cols)

    def handle_add_source(self, source: DataSource):
        if self.plot_grid.add_source(source):
            # Update a
            sel_channels = self.common_config.get_param("display_sources")
            sel_channels.append(source)
            self.common_config.set_param("display_sources", list(set(sel_channels)))
            # Change state of UI
            self.experiment_settings_panel.update_source("add", source)

    def handle_remove_source(self, source: DataSource):
        if self.plot_grid.remove_source(source):
            # Update config
            sel_channels = self.common_config.get_param("display_sources")
            sel_channels.remove(source)
            self.common_config.set_param("display_sources", sel_channels)
            # Change state of UI
            self.experiment_settings_panel.update_source("remove", source)
    
    # State update handlers 
    def on_server_connected(self):
        """Handle server connection"""
        self.device_status_panel.update_server_status(True)
        self.log_display_panel.log_message("info", "Connected to server")
        
        # Auto-ping
        if self.client_worker:
            self.client_worker.ping_server()
    
    def on_server_disconnected(self):
        """Handle server disconnection"""
        self.device_status_panel.update_server_status(False)
        self.log_display_panel.log_message("warning", "Disconnected from server")
        
    def handle_connection_requested(self): 
        if self.client_worker: 
            for device_id in self.devices.keys(): 
                self.device_status_panel.update_device_state(device_id, ConnectionStatus.CONNECTING)
                self.client_worker.connect_device(device_id=device_id)
    
    def handle_device_connected(self, device_id):         
        if device_id is not None:
            self.devices[device_id]['state'] = ConnectionStatus.CONNECTED
            self.device_status_panel.update_device_state(device_id, ConnectionStatus.CONNECTED)
        else:
            # In this case all devices were requested for connection 
            for device_id in self.devices.keys():
                self.devices[device_id]['state'] = ConnectionStatus.CONNECTED
                self.device_status_panel.update_device_state(device_id, ConnectionStatus.CONNECTED)
        
        # Check if all are connected and if so, disable UI buttons 
        self.update_buttons()
        
    def handle_device_connection_failed(self, device_id): 
        if device_id is not None:
            self.devices[device_id]['state'] = ConnectionStatus.DISCONNECTED
            self.device_status_panel.update_device_state(device_id, ConnectionStatus.DISCONNECTED)
        else:
            # In this case all devices were requested for connection 
            for device_id in self.devices.keys():
                self.devices[device_id]['state'] = ConnectionStatus.DISCONNECTED
                self.device_status_panel.update_device_state(device_id, ConnectionStatus.DISCONNECTED)
          
        self.update_buttons()
      
    def handle_device_disconnected(self): 
        # Disconnect devices
        for device_id in self.devices.keys(): 
            self.devices[device_id]['state'] = ConnectionStatus.DISCONNECTED
            self.device_status_panel.update_device_state(device_id, ConnectionStatus.DISCONNECTED)
      
        self.update_buttons()
       
    def handle_streaming_start_requested(self): 
        if self.client_worker: 
            self.client_worker.start_streaming()
            self.running_status = RunningStatus.RUNNING
    
    def handle_streaming_stop_requested(self): 
        if self.client_worker: 
            self.client_worker.stop_streaming()
            self.running_status = RunningStatus.STOPPED
     
    def update_save_state(self):
        self.saving_status = True  
        if self.client_worker: 
            pass 
    
    def toggle_instructions(self, flag):
        self.enable_instructions = flag
        if self.instruction_dialog is not None:
            self.instruction_dialog.toggle_ui(self.enable_instructions)
    
    def update_buttons(self): 
        connected = True 
        for device_dict in self.devices.values(): 
            if device_dict['state'] == ConnectionStatus.DISCONNECTED: 
                connected = False 
                break 
        
        if connected: 
            self.app_control_panel.update_button_states(ConnectionStatus.CONNECTED, RunningStatus.STOPPED)
        else: 
            self.app_control_panel.update_button_states(ConnectionStatus.DISCONNECTED, RunningStatus.NOINIT)
    
     
if __name__ == "__main__":
    # import qdarktheme

    # qdarktheme.enable_hi_dpi()
    app = QApplication(sys.argv)
    # qdarktheme.setup_theme(theme = 'auto')

    # Create and show main window
    window = BioViewMonitor()
    window.show()
    
    sys.exit(app.exec())
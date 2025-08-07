'''
Provides functionality for running experiments using a routine-based paradigm. Supplementary instructions 
may be provided to guide the experimental protocol and include the following - 
1. Audio-Based Instructions 
2. Text-Based Instructions 

Since each instruction handler is implemented as an independent QObject with the same specification, we
are free to implement as many instruction types as needed. 
'''
from PyQt6.QtCore import QThread, pyqtSignal

from .audio_player import AudioPlayer
from .text_instructions import TextInstructions

AVAILABLE_INSTRUCTIONS = {
    'audio': AudioPlayer,
    'text': TextInstructions
}

class RoutineControl(): 
    pass 

class InstructionHandler(QThread):
    logEvent = pyqtSignal(str, str)
    textUpdate = pyqtSignal(str)  # Signal to update instruction text
    showDialog = pyqtSignal()  # Signal to show the dialog
    hideDialog = pyqtSignal()  # Signal to hide the dialog

    def __init__(
        self, 
        instruction_type: str,  
        instruction_file: str, 
        loop_instruction: bool = False, 
        parent=None
    ):
        super().__init__(parent)
        # TODO: Add UI for running a routine (provided in common configuration earlier) along with a dropdown to select which one.
        # TODO: Preferably, let this be done in a pop-up UI for convenience
        
        if instruction_type not in AVAILABLE_INSTRUCTIONS.keys():
            self.logEvent.emit("error", f"Invalid instruction specified: {instruction_type}")
            return 

        self.instruction_type = instruction_type
        self.instruction_handler = AVAILABLE_INSTRUCTIONS[instruction_type](
            instruction_file = instruction_file, 
            loop_instruction = loop_instruction,
            parent = self
        )
        
        self.running = False


    def run(self):
        if self.instruction_handler is None:
            self.logEvent.emit("warning", "No instruction handler available")
            return

        self.running = True

        # This may include file-reading, etc one time tasks before running that won't loop
        if hasattr(self.instruction_handler, "pre_run"):
            self.instruction_handler.pre_run()

        if self.instruction_type == "text":
            self.showDialog.emit()

        while self.running:
            should_stop = self.instruction_handler.run()
            if should_stop:
                break

        self.stop()

    def stop(self):
        self.running = False

        if self.instruction_handler is not None:
            self.instruction_handler.stop()

        # Hide text dialog
        if self.instruction_type == "text":
            self.hideDialog.emit()

import time 
from pathlib import Path
from typing import Tuple

from PyQt6.QtCore import QObject, pyqtSignal

def parse_instruction_line(line: str) -> Tuple[float, str]:
    """
    Parse a line in format: (duration, text)
    Returns tuple of (duration_seconds, text_content)
    """
    line = line.strip()
    if not line or line.startswith('#'):  # Skip empty lines and comments
        return None, None
    
    try:
        # Find the first comma to split duration and text
        if not (line.startswith('(') and ')' in line):
            raise ValueError("Line must be in format: (duration, text)")
        
        # Extract content within parentheses
        paren_end = line.find(')')
        if paren_end == -1:
            raise ValueError("Missing closing parenthesis")
        
        content = line[1:paren_end]  # Remove opening parenthesis
        remaining_text = line[paren_end + 1:].strip()
        
        # Split on first comma
        if ',' not in content:
            raise ValueError("Missing comma separator in format (duration, text)")
        
        parts = content.split(',', 1)
        duration_str = parts[0].strip()
        text_from_parens = parts[1].strip() if len(parts) > 1 else ""
        
        # Combine text from parentheses with any text after closing paren
        full_text = (text_from_parens + " " + remaining_text).strip()
        
        # Parse duration
        duration = float(duration_str)
        
        return duration, full_text
        
    except (ValueError, IndexError) as e:
        print(f"Error parsing line: '{line}' - {e}")
        return None, None
    
class TextInstructions(QObject):
    textUpdate = pyqtSignal(str)

    def __init__(
        self, 
        instruction_file: str, 
        loop_instruction: bool = False, 
        parent=None
    ):
        super().__init__(parent=parent)

        if instruction_file is None or not Path(instruction_file).exists():
            raise Exception("Text instructions not found.")
        
        self.instruction_file = Path(instruction_file)
        
        # Instructions will be stored as (interval, line) pairs in the text file 
        self.instructions = []
        try:
            with open(self.instruction_file, 'r', encoding='utf-8') as file:
                for line_num, line in enumerate(file, 1):
                    duration, text = parse_instruction_line(line)
                    if duration is not None and text is not None:
                        self.instructions.append((duration, text))
                    elif line.strip() and not line.strip().startswith('#'):
                        print(f"Warning: Skipped malformed line {line_num}: '{line.strip()}'")
        except Exception as e:
            print(f"Error reading file '{self.instruction_file}': {e}")
            return 
    
        try:
            self.instructions = open(self.instruction_file).read().splitlines()
        except Exception:
            return

        self.current_index = 0
        self.loop_instruction = loop_instruction
        
        self.running = False

    def run(self):
        self.running = True 
        
        idx = 0
        while self.running: 
            # If reached end of instructions, check whether we should loop or not
            if idx == len(self.instructions):
                if self.loop_instruction: 
                    idx = 0 
                else: 
                    return 

            # Get current instruction
            (duration, text) = self.instructions[idx]
            idx += 1

            # Send signal to update text in UI
            self.textUpdate.emit(text)
             
            # Wait for specified duration
            time.sleep(duration)

    def stop(self):
        self.running = False
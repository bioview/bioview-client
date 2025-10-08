import contextlib
import logging
import os
from pathlib import Path

from PyQt6.QtCore import QObject


os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"  # noqa: E402
import pygame  # noqa: E402


logger = logging.getLogger(__name__)


class AudioPlayer(QObject):
    def __init__(
        self, instruction_file: str, loop_instruction: bool = False, parent=None
    ):
        super().__init__(parent=parent)

        if instruction_file is None or not Path(instruction_file).exists():
            raise Exception("Audio instructions not found.")

        self.instruction_file = Path(instruction_file)
        self.loop_instruction = loop_instruction

        # Initialize pygame mixer
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except pygame.error as e:
            logger.warning("pygame mixer init issue: %s", e)

        try:
            pygame.mixer.music.load(self.instruction_file)
        except Exception as e:
            logger.exception("Error loading audio file: %s", e)

        self.running = False

    def run(self):
        while self.running:
            try:
                pygame.mixer.music.play()
            except Exception as e:
                logger.exception("Error playing audio: %s", e)
                break

            # Wait for music to finish or stop signal
            while pygame.mixer.music.get_busy():
                self.thread().msleep(100)

            # If not looping, break after one play
            if not self.loop_instruction:
                break

        return True

    def stop(self):
        self.running = False

        with contextlib.suppress(Exception):
            pygame.mixer.music.stop()

"""
Data model + parsing for timed-mode routines declared in the experiment config.

A timed mode pairs a fixed run duration with an optional instruction (audio,
video, or text) that is presented while data is collected. These are pure data
holders; playback is handled by ``InstructionController``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


# Supported instruction kinds
INSTRUCTION_AUDIO = "audio"
INSTRUCTION_VIDEO = "video"
INSTRUCTION_TEXT = "text"
SUPPORTED_INSTRUCTION_TYPES = (INSTRUCTION_AUDIO, INSTRUCTION_VIDEO, INSTRUCTION_TEXT)

DEFAULT_TEXT_FONT_SIZE = 28


def parse_duration(value) -> float:
    """Convert a duration specification into seconds.

    Accepts a number (interpreted as seconds), a unit-suffixed string such as
    ``"3m"``, ``"90s"`` or ``"1h"``, or a clock string such as ``"mm:ss"`` /
    ``"hh:mm:ss"``. Raises ``ValueError`` for anything unparseable.
    """
    if value is None:
        raise ValueError("Duration is required for a timed mode")

    if isinstance(value, bool):  # guard against True/False slipping through
        raise ValueError(f"Invalid duration: {value!r}")

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        raise ValueError("Duration is empty")

    # Clock format hh:mm:ss or mm:ss
    if ":" in text:
        seconds = 0.0
        for part in text.split(":"):
            seconds = seconds * 60 + float(part)
        return seconds

    # Unit-suffixed (h/m/s)
    units = {"h": 3600.0, "m": 60.0, "s": 1.0}
    if text[-1] in units:
        return float(text[:-1]) * units[text[-1]]

    # Bare number string -> seconds
    return float(text)


@dataclass
class InstructionSpec:
    """A single instruction to present during a timed mode."""

    type: str
    file: str
    loop: bool = False
    font_size: int = DEFAULT_TEXT_FONT_SIZE
    line_gap: Optional[float] = None  # seconds between lines; None => all at once


@dataclass
class TimedMode:
    """A pre-defined routine: run streaming for ``duration`` seconds, optionally
    presenting ``instruction``."""

    label: str
    duration: float
    instruction: Optional[InstructionSpec] = None


def _resolve_path(file_path: str, base_dir: Optional[Path]) -> str:
    """Resolve an instruction file path. Absolute paths are used as-is; relative
    paths are resolved against the config file's directory when provided."""
    if not file_path:
        return file_path
    path = Path(file_path).expanduser()
    if path.is_absolute() or base_dir is None:
        return str(path)
    return str((base_dir / path).resolve())


def parse_instruction(raw: Optional[dict], base_dir: Optional[Path] = None) -> Optional[InstructionSpec]:
    if not raw or not isinstance(raw, dict):
        return None

    instr_type = str(raw.get("type", "")).strip().lower()
    if instr_type not in SUPPORTED_INSTRUCTION_TYPES:
        raise ValueError(f"Unsupported instruction type: {raw.get('type')!r}")

    file_path = _resolve_path(raw.get("file", ""), base_dir)

    line_gap = raw.get("line_gap", None)
    if line_gap is not None:
        line_gap = float(line_gap)

    return InstructionSpec(
        type=instr_type,
        file=file_path,
        loop=bool(raw.get("loop", False)),
        font_size=int(raw.get("font_size", DEFAULT_TEXT_FONT_SIZE)),
        line_gap=line_gap,
    )


def parse_timed_modes(raw_modes, base_dir: Optional[Path] = None) -> List[TimedMode]:
    """Parse the raw ``timed_modes`` list from the experiment config into a list
    of ``TimedMode`` objects. Malformed entries are skipped (callers should log)."""
    modes: List[TimedMode] = []
    if not raw_modes or not isinstance(raw_modes, (list, tuple)):
        return modes

    for idx, raw in enumerate(raw_modes):
        if not isinstance(raw, dict):
            continue
        try:
            label = str(raw.get("label") or f"Routine {idx + 1}")
            duration = parse_duration(raw.get("duration"))
            instruction = parse_instruction(raw.get("instruction"), base_dir)
            modes.append(TimedMode(label=label, duration=duration, instruction=instruction))
        except (ValueError, TypeError):
            # Skip malformed routine; keep the rest usable
            continue

    return modes

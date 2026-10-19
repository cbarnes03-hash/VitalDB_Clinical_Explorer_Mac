"""Time parsing/formatting helpers (all times are seconds since start of recording)."""
from __future__ import annotations

import re


class TimeParseError(ValueError):
    pass


_NUM = re.compile(r"^\s*[-+]?\d+(\.\d+)?\s*$")


def parse_time(text: str) -> float:
    """Parse a clinician-friendly time into seconds.

    Accepted forms:
        ``12.5``        -> 12.5 minutes
        ``12:30``       -> 12 min 30 s
        ``1:02:30``     -> 1 h 2 min 30 s
        ``90s``         -> 90 seconds
        ``15m``/``15 min`` -> 15 minutes;  ``1h``/``1 h`` -> 1 hour
    """
    if text is None or not str(text).strip():
        raise TimeParseError("Please enter a time (for example 12:30 or 15 min).")
    s = str(text).strip().lower()
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?)$", s)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit.startswith("s"):
            return val
        if unit.startswith("m"):
            return val * 60.0
        return val * 3600.0
    if ":" in s:
        parts = s.split(":")
        if len(parts) not in (2, 3) or not all(_NUM.match(p) for p in parts):
            raise TimeParseError(f"Could not understand the time '{text}'. Use mm:ss or h:mm:ss.")
        nums = [float(p) for p in parts]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if _NUM.match(s):
        return float(s) * 60.0  # bare numbers are minutes
    raise TimeParseError(f"Could not understand the time '{text}'. Try 12:30, 15 min or 1:02:30.")


def format_hms(seconds: float, decimals: int = 0) -> str:
    if seconds is None:
        return ""
    sign = "-" if seconds < 0 else ""
    seconds = abs(float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if decimals:
        return f"{sign}{h}:{m:02d}:{s:0{3 + decimals}.{decimals}f}"
    return f"{sign}{h}:{m:02d}:{int(round(s)) % 60:02d}" if round(s) < 60 else f"{sign}{h}:{m + 1:02d}:00"


def format_duration(seconds: float) -> str:
    if seconds is None:
        return "unknown"
    return f"{format_hms(seconds)} ({seconds / 60:.1f} min)"

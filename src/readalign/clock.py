"""SMIL 3.0 clock values, as used by ``clipBegin``/``clipEnd`` and ``media:duration``.

The full SMIL grammar allows several forms; readalign writes the ``H:MM:SS.mmm`` full clock
value everywhere and reads back every form it may meet in a third-party package.
"""

from __future__ import annotations

import re

_FULL = re.compile(r"^(\d+):([0-5]\d):([0-5]\d(?:\.\d+)?)$")
_PARTIAL = re.compile(r"^([0-5]?\d):([0-5]\d(?:\.\d+)?)$")
_TIMECOUNT = re.compile(r"^(\d+(?:\.\d+)?)\s*(h|min|s|ms)?$")

_UNIT_SECONDS = {"h": 3600.0, "min": 60.0, "s": 1.0, "ms": 0.001, None: 1.0}


def format_clock(seconds: float) -> str:
    """Render seconds as a SMIL full clock value, e.g. ``0:12:34.120``."""
    if seconds < 0:
        seconds = 0.0
    # Round to milliseconds first so 59.9996 does not render as 0:00:60.000.
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours}:{minutes:02d}:{secs:02d}.{millis:03d}"


def parse_clock(value: str) -> float:
    """Parse any SMIL clock value into seconds."""
    text = value.strip()
    match = _FULL.match(text)
    if match:
        return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    match = _PARTIAL.match(text)
    if match:
        return int(match[1]) * 60 + float(match[2])
    match = _TIMECOUNT.match(text)
    if match:
        return float(match[1]) * _UNIT_SECONDS[match[2]]
    raise ValueError(f"not a SMIL clock value: {value!r}")

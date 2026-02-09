from __future__ import annotations

"""
Shared state for the FastAPI backend.

We keep this intentionally simple (in-memory, single-process demo):
- One active pipeline runner thread
- Latest annotated JPEG frame for MJPEG streaming
- Latest sessions list
- Entry/Exit line points (set from the browser)
- Zones (4 points + name) for tracking visits
"""

import threading
from dataclasses import dataclass
from typing import Optional, Tuple


Point = Tuple[int, int]


Line = Tuple[Point, Point]  # (p1, p2)


@dataclass
class Zone:
    """A zone defined by 4 points (rectangle) and a name."""
    name: str
    points: Tuple[Point, Point, Point, Point]  # 4 corners of the zone


@dataclass
class SharedState:
    # Lines: list of (p1, p2) tuples for multiple entry/exit points
    lines: list[Line] = None  # type: ignore[assignment]
    
    # Legacy single line support (for backward compatibility)
    line_p1: Optional[Point] = None
    line_p2: Optional[Point] = None

    # Zones: list of Zone objects for tracking visits
    zones: list[Zone] = None  # type: ignore[assignment]

    # Latest JPEG bytes for MJPEG streaming
    latest_jpeg: Optional[bytes] = None

    # Latest sessions snapshot (list of dicts)
    latest_sessions: list[dict] = None  # type: ignore[assignment]

    # Frame size (set once we decode first frame)
    frame_w: Optional[int] = None
    frame_h: Optional[int] = None

    # Runner control
    running: bool = False
    stop_flag: bool = False
    lock: threading.Lock = threading.Lock()
    frame_ready: threading.Condition = threading.Condition(lock)

    def __post_init__(self) -> None:
        if self.latest_sessions is None:
            self.latest_sessions = []
        if self.lines is None:
            self.lines = []
        if self.zones is None:
            self.zones = []
    
    def get_all_lines(self) -> list[Line]:
        """Get all lines, including legacy single line if present."""
        result = list(self.lines) if self.lines else []
        # Add legacy single line if it exists and isn't already in the list
        if self.line_p1 is not None and self.line_p2 is not None:
            legacy_line = (self.line_p1, self.line_p2)
            if legacy_line not in result:
                result.append(legacy_line)
        return result
    
    def get_all_zones(self) -> list[Zone]:
        """Get all zones."""
        return list(self.zones) if self.zones else []


STATE = SharedState()



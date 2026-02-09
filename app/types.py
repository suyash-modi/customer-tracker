from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class Detection:
    """Single person detection in absolute pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int
    conf: float

    @property
    def w(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def h(self) -> int:
        return max(0, self.y2 - self.y1)


@dataclass
class Track:
    """
    Tracking output record used by visualization + session manager.

    `embedding` is the appearance descriptor used for stitching.
    """

    track_id: int
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float
    embedding: np.ndarray

    # Filled later by session stitching
    global_person_id: Optional[int] = None
    session_id: Optional[str] = None
    cross_event: Optional[str] = None



from __future__ import annotations

"""
Mandatory feature: user selects exactly 2 points on the first frame to define an ENTRY/EXIT boundary line.

Implementation notes:
- Uses OpenCV mouse callback.
- Blocks until 2 clicks are collected.
- Returns (p1, p2) as integer pixel coordinates.
"""

from typing import List, Tuple

import cv2
import numpy as np


Point = Tuple[int, int]


def select_line_on_first_frame(first_frame_bgr: np.ndarray) -> tuple[Point, Point]:
    pts: List[Point] = []
    win = "Select ENTRY/EXIT line (click 2 points)"

    frame = first_frame_bgr.copy()

    def on_mouse(event, x, y, _flags, _param):
        nonlocal frame
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 2:
            pts.append((int(x), int(y)))
            frame = first_frame_bgr.copy()
            # Draw points and interim line
            for p in pts:
                cv2.circle(frame, p, 6, (0, 255, 255), -1)
            if len(pts) == 2:
                cv2.line(frame, pts[0], pts[1], (0, 255, 255), 2)

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)

    while True:
        show = frame.copy()
        cv2.putText(
            show,
            "Click 2 points for ENTRY/EXIT line. Press 'r' to reset.",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
        )
        cv2.imshow(win, show)
        k = cv2.waitKey(20) & 0xFF
        if k == ord("r"):
            pts.clear()
            frame = first_frame_bgr.copy()
        if len(pts) == 2:
            break

    cv2.destroyWindow(win)
    return pts[0], pts[1]



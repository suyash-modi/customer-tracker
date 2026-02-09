from __future__ import annotations

"""
OpenCV visualization:
- Bounding boxes
- Track ID
- Session ID
- Entry/Exit line
- ENTRY / EXIT event text
- Zones (polygons with names)
"""

from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.session_manager import Session
from app.types import Track
from backend.app_state import Zone


Point = Tuple[int, int]


class Visualizer:
    def __init__(self, lines: list[tuple[Point, Point]], zones: Optional[List[Zone]] = None, show_fps: bool = False):
        """
        Initialize with multiple lines and zones.
        
        Args:
            lines: List of (p1, p2) tuples, each representing an entry/exit line
            zones: List of Zone objects to draw
        """
        self.lines = [(tuple(map(int, p1)), tuple(map(int, p2))) for p1, p2 in lines]
        self.zones = zones or []
        self.show_fps = show_fps

    def side_of_line(self, p: Point, line_idx: int = 0) -> int:
        """
        Returns which side of the directed line (p1 -> p2) point p is on:
        -1, 0, +1 based on cross product sign.
        
        Args:
            p: Point to check
            line_idx: Which line to check (default: first line for backward compatibility)
        """
        if not self.lines or line_idx >= len(self.lines):
            return 0
        p1, p2 = self.lines[line_idx]
        x, y = p
        x1, y1 = p1
        x2, y2 = p2
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if abs(cross) < 1e-6:
            return 0
        return 1 if cross > 0 else -1
    
    def check_all_lines(self, p: Point) -> list[tuple[int, int]]:
        """
        Check which side of each line the point is on.
        
        Returns:
            List of (line_idx, side) tuples where side is -1, 0, or +1
        """
        results = []
        for idx, (p1, p2) in enumerate(self.lines):
            side = self.side_of_line(p, idx)
            results.append((idx, side))
        return results

    def draw(self, frame_bgr: np.ndarray, tracks: list[Track], active_sessions: Dict[int, Session], fps: float) -> np.ndarray:
        out = frame_bgr.copy()

        # Draw all zones
        zone_colors = [(255, 100, 100), (100, 255, 100), (100, 100, 255), (255, 255, 100), (255, 100, 255)]
        for idx, zone in enumerate(self.zones):
            color = zone_colors[idx % len(zone_colors)]
            points = np.array(zone.points, dtype=np.int32)
            # Draw filled polygon with transparency
            overlay = out.copy()
            cv2.fillPoly(overlay, [points], color)
            cv2.addWeighted(overlay, 0.3, out, 0.7, 0, out)
            # Draw border
            cv2.polylines(out, [points], True, color, 2)
            # Draw zone name
            if points.size > 0:
                center_x = int(np.mean(points[:, 0]))
                center_y = int(np.mean(points[:, 1]))
                cv2.putText(out, zone.name, (center_x - 30, center_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        # Draw all entry/exit lines
        colors = [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 255, 0), (255, 0, 0)]  # Yellow, Magenta, Cyan, Green, Red
        for idx, (p1, p2) in enumerate(self.lines):
            color = colors[idx % len(colors)]
            cv2.line(out, p1, p2, color, 2)
            
            # Draw arrow to show direction (p1 -> p2)
            # Calculate arrow tip
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            length = np.sqrt(dx*dx + dy*dy)
            if length > 0:
                # Normalize and scale arrow
                unit_x = dx / length
                unit_y = dy / length
                arrow_length = min(30, length * 0.1)
                arrow_tip = (int(p2[0] - unit_x * arrow_length), int(p2[1] - unit_y * arrow_length))
                
                # Draw arrow (small triangle)
                arrow_size = 8
                perp_x = -unit_y * arrow_size
                perp_y = unit_x * arrow_size
                arrow_p1 = (int(arrow_tip[0] + perp_x), int(arrow_tip[1] + perp_y))
                arrow_p2 = (int(arrow_tip[0] - perp_x), int(arrow_tip[1] - perp_y))
                cv2.fillPoly(out, [np.array([p2, arrow_p1, arrow_p2], dtype=np.int32)], color)
            
            # Label with entry/exit sides
            label = f"LINE {idx + 1}" if len(self.lines) > 1 else "ENTRY/EXIT LINE"
            cv2.putText(out, label, (p1[0] + 10, p1[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            
            # Show which side is entry/exit
            # Right side of line (when facing p1->p2) is ENTRY side (-1)
            # Left side of line (when facing p1->p2) is EXIT side (+1)
            mid_x = (p1[0] + p2[0]) // 2
            mid_y = (p1[1] + p2[1]) // 2
            
            # Calculate perpendicular vector for offset
            if length > 0:
                perp_x_norm = -unit_y
                perp_y_norm = unit_x
                offset = 25
                
                # Right side (ENTRY) - offset to the right
                entry_x = int(mid_x + perp_x_norm * offset)
                entry_y = int(mid_y + perp_y_norm * offset)
                cv2.putText(out, "ENTRY", (entry_x, entry_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                
                # Left side (EXIT) - offset to the left
                exit_x = int(mid_x - perp_x_norm * offset)
                exit_y = int(mid_y - perp_y_norm * offset)
                cv2.putText(out, "EXIT", (exit_x, exit_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        for tr in tracks:
            color = (0, 255, 0)
            if tr.cross_event == "ENTRY":
                color = (0, 200, 255)
            elif tr.cross_event == "EXIT":
                color = (0, 0, 255)

            cv2.rectangle(out, (tr.x1, tr.y1), (tr.x2, tr.y2), color, 2)
            # Always show customer ID if available, otherwise show track ID
            if tr.session_id:
                label = f"{tr.session_id}"
            else:
                label = f"T{tr.track_id}"
            cv2.putText(out, label, (tr.x1, max(20, tr.y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            if tr.cross_event:
                cv2.putText(out, tr.cross_event, (tr.x1, tr.y2 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        # Small HUD
        y = 30
        cv2.putText(out, f"Active sessions: {len(active_sessions)}", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        y += 30
        if self.show_fps:
            cv2.putText(out, f"FPS: {fps:.1f}", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        return out



from __future__ import annotations

"""
Minimal in-repo tracker (no DeepSORT / no external tracking package).

User requested: "dont use deepsort" and "dont replace it remove it completely".
So we implement a tiny greedy IoU-based tracker for a single-camera demo.

How it works (MVP):
- Each frame provides person detections.
- We match detections to existing tracks using IoU (greedy highest IoU first).
- Unmatched detections start new tracks.
- Tracks expire after `max_age` frames without a match.

Important:
- This is not production tracking. It's intentionally simple and easy to debug.
- ReID embeddings are still extracted per detection and attached to the resulting track
  so the session stitching logic can use cosine similarity.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from app.types import Detection, Track


def _iou_xyxy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    iw = max(0, x2 - x1)
    ih = max(0, y2 - y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


@dataclass
class _InternalTrack:
    track_id: int
    bbox: Tuple[int, int, int, int]  # x1,y1,x2,y2
    age: int = 0
    time_since_update: int = 0


class SimpleIoUTracker:
    """
    Tiny tracker that returns Track objects per frame.

    `track_id` is the per-video track identifier used by the rest of the pipeline.
    """

    def __init__(self, iou_threshold: float = 0.3, max_age: int = 15):
        self.iou_threshold = float(iou_threshold)
        self.max_age = int(max_age)
        self._next_id = 1
        self._tracks: Dict[int, _InternalTrack] = {}

    def update(self, detections: List[Detection], embeddings: np.ndarray, frame_shape) -> List[Track]:
        # Age all tracks
        for tr in self._tracks.values():
            tr.age += 1
            tr.time_since_update += 1

        det_boxes = [(d.x1, d.y1, d.x2, d.y2) for d in detections]
        det_used = [False] * len(detections)

        # Greedy matching: compute all (track, det) IoUs, sort desc, take best non-conflicting pairs.
        pairs = []
        for tid, tr in self._tracks.items():
            for di, box in enumerate(det_boxes):
                pairs.append((tid, di, _iou_xyxy(tr.bbox, box)))
        pairs.sort(key=lambda x: x[2], reverse=True)

        matched_tracks = set()
        for tid, di, iou in pairs:
            if iou < self.iou_threshold:
                break
            if tid in matched_tracks or det_used[di]:
                continue
            # match
            tr = self._tracks[tid]
            tr.bbox = det_boxes[di]
            tr.time_since_update = 0
            matched_tracks.add(tid)
            det_used[di] = True

        # Create new tracks for unmatched detections
        for di, used in enumerate(det_used):
            if used:
                continue
            tid = self._next_id
            self._next_id += 1
            self._tracks[tid] = _InternalTrack(track_id=tid, bbox=det_boxes[di], age=1, time_since_update=0)
            matched_tracks.add(tid)
            det_used[di] = True

        # Prune old tracks
        to_del = [tid for tid, tr in self._tracks.items() if tr.time_since_update > self.max_age]
        for tid in to_del:
            del self._tracks[tid]

        # Build output Track list. We attach embedding by choosing the detection with max IoU for that track this frame.
        out: List[Track] = []
        for tid, tr in self._tracks.items():
            x1, y1, x2, y2 = tr.bbox

            # Find the best detection index for this track (for embedding); if none, zero-vector.
            best_di = -1
            best_iou = 0.0
            for di, box in enumerate(det_boxes):
                iou = _iou_xyxy(tr.bbox, box)
                if iou > best_iou:
                    best_iou = iou
                    best_di = di

            if best_di >= 0 and embeddings is not None and best_di < embeddings.shape[0]:
                emb = np.asarray(embeddings[best_di], dtype=np.float32).reshape(-1)
                conf = float(detections[best_di].conf)
            else:
                emb = np.zeros((256,), dtype=np.float32)
                conf = 1.0

            out.append(
                Track(
                    track_id=int(tid),
                    x1=int(x1),
                    y1=int(y1),
                    x2=int(x2),
                    y2=int(y2),
                    conf=conf,
                    embedding=emb,
                )
            )

        return out



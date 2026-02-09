from __future__ import annotations

"""
Headless pipeline runner for browser-based UI.

Runs the same core pipeline as `main.py`, but:
- no OpenCV windows
- writes the annotated frame into shared state as JPEG for MJPEG streaming
- exposes sessions snapshot
"""

import threading
import time
from typing import Optional

import cv2
import numpy as np

from app.config import AppConfig
from app.openvino_models import OpenVINOModels
from app.session_manager import SessionManager
from app.tracker import SimpleIoUTracker
from app.utils import point_in_polygon
from app.video_input import open_video_source
from app.visualizer import Visualizer

from backend.app_state import STATE


def _encode_jpeg(frame_bgr: np.ndarray, quality: int = 80) -> Optional[bytes]:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return None
    return buf.tobytes()


def start_pipeline_if_needed(cfg: AppConfig, source: str) -> None:
    """
    Starts (or restarts) the background pipeline thread.

    Earlier versions simply ignored new configs while a pipeline was running,
    which meant if you first started with source=0 and later chose a YouTube
    URL, the backend would keep using the original camera source.

    Here we:
    - signal any existing runner to stop
    - wait until it has fully stopped
    - reset shared state
    - start a fresh runner with the new source
    """
    # Ask any existing pipeline to stop
    with STATE.lock:
        if STATE.running:
            STATE.stop_flag = True

    # Wait for previous runner to exit
    while True:
        with STATE.lock:
            if not STATE.running:
                # Reset shared state for the new run
                STATE.line_p1 = None
                STATE.line_p2 = None
                STATE.latest_jpeg = None
                STATE.latest_sessions = []
                STATE.frame_w = None
                STATE.frame_h = None
                STATE.stop_flag = False
                STATE.running = True
                break
        time.sleep(0.05)

    # Start fresh runner
    t = threading.Thread(target=_run_loop, args=(cfg, source), daemon=True)
    t.start()


def stop_pipeline() -> None:
    with STATE.lock:
        STATE.stop_flag = True


def _run_loop(cfg: AppConfig, source: str) -> None:
    cap = None
    try:
        cap = open_video_source(source)
        ok, first = cap.read()
        if not ok or first is None:
            raise RuntimeError("Could not read from video source.")

        h, w = first.shape[:2]
        with STATE.lock:
            STATE.frame_w = w
            STATE.frame_h = h
            # Immediately show the first raw frame so the browser UI can display video
            first_jpg = _encode_jpeg(first)
            if first_jpg is not None:
                STATE.latest_jpeg = first_jpg
                STATE.latest_sessions = []
                STATE.frame_ready.notify_all()

        models = OpenVINOModels(cfg)
        tracker = SimpleIoUTracker()
        sessions = SessionManager(reid_cosine_threshold=cfg.reid_cosine_threshold)

        # Visualization will be created lazily once the line has been set from the UI.
        vis: Optional[Visualizer] = None

        # per-track line side memory (only meaningful after line is set)
        track_last_side: dict[int, int] = {}
        track_last_cross_ts: dict[int, float] = {}
        
        # per-track zone state: track_id -> set of zone names currently in
        track_current_zones: dict[int, set[str]] = {}

        prev_ts = time.time()

        # process first frame and then continue streaming
        frame = first
        while True:
            if frame is None:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

            with STATE.lock:
                if STATE.stop_flag:
                    break
                all_lines = STATE.get_all_lines()
                all_zones = STATE.get_all_zones()

            # If no lines are defined, just stream the raw frame.
            if not all_lines:
                jpg = _encode_jpeg(frame)
                if jpg is not None:
                    with STATE.lock:
                        STATE.latest_jpeg = jpg
                        # sessions list stays as-is (likely empty)
                        STATE.frame_ready.notify_all()
                frame = None
                continue

            # Lines are available: ensure visualizer is initialized and updated.
            # Also check if zones changed
            zones_changed = vis is None or (hasattr(vis, 'zones') and len(vis.zones) != len(all_zones))
            if vis is None or len(vis.lines) != len(all_lines) or zones_changed:
                vis = Visualizer(lines=all_lines, zones=all_zones, show_fps=True)

            dets = models.detect_persons(frame, conf_threshold=cfg.det_conf_threshold)
            embeds = []
            for d in dets:
                crop = frame[d.y1 : d.y2, d.x1 : d.x2]
                embeds.append(models.extract_reid_embedding(crop))
            embeds_np = np.stack(embeds, axis=0) if embeds else np.zeros((0, models.reid_dim), dtype=np.float32)

            tracks = tracker.update(dets, embeds_np, frame_shape=frame.shape)

            for tr in tracks:
                pid = sessions.assign_identity(track_id=tr.track_id, embedding=tr.embedding)

                cx = int((tr.x1 + tr.x2) / 2)
                cy = int((tr.y1 + tr.y2) / 2)
                
                # Check all lines for crossings
                current_sides = vis.check_all_lines((cx, cy))
                
                # Get last known sides for this track (dict: line_idx -> side)
                track_key = tr.track_id
                last_sides_dict = track_last_side.get(track_key, {})
                
                event = None
                # Check each line for crossings
                for line_idx, current_side in current_sides:
                    last_side = last_sides_dict.get(line_idx, current_side)
                    
                    if current_side != 0 and last_side != 0 and current_side != last_side:
                        # -1 -> +1 is ENTRY, +1 -> -1 is EXIT
                        potential_event = "ENTRY" if last_side < current_side else "EXIT"
                        now = time.time()
                        if now - track_last_cross_ts.get(track_key, 0.0) >= 0.75:
                            event = potential_event
                            track_last_cross_ts[track_key] = now
                            break  # Only trigger one event per frame
                
                # Update last known sides for all lines
                new_sides_dict = {line_idx: side for line_idx, side in current_sides}
                track_last_side[track_key] = new_sides_dict

                if event == "ENTRY":
                    sessions.on_entry(pid)
                elif event == "EXIT":
                    sessions.on_exit(pid)
                
                # Check zone visits
                current_zones = set()
                for zone in all_zones:
                    if point_in_polygon((cx, cy), zone.points):
                        current_zones.add(zone.name)
                
                # Get previous zones for this track
                prev_zones = track_current_zones.get(track_key, set())
                
                # Detect zone entries (new zones)
                for zone_name in current_zones - prev_zones:
                    sessions.on_zone_entry(pid, zone_name)
                
                # Detect zone exits (zones no longer in)
                for zone_name in prev_zones - current_zones:
                    sessions.on_zone_exit(pid, zone_name)
                
                # Update current zones for this track
                track_current_zones[track_key] = current_zones

                tr.global_person_id = pid
                tr.session_id = sessions.get_session_id(pid)
                tr.cross_event = event

            now = time.time()
            fps = 1.0 / max(1e-6, (now - prev_ts))
            prev_ts = now

            # Mark inactive any sessions where person hasn't been seen recently
            sessions.mark_inactive_if_not_seen()
            
            out = vis.draw(frame, tracks, sessions.active_sessions(), fps=fps) if vis is not None else frame
            jpg = _encode_jpeg(out)
            if jpg is not None:
                with STATE.lock:
                    STATE.latest_jpeg = jpg
                    STATE.latest_sessions = sessions.all_sessions()
                    STATE.frame_ready.notify_all()

            frame = None

        with STATE.lock:
            STATE.latest_sessions = sessions.all_sessions()

    finally:
        if cap is not None:
            cap.release()
        with STATE.lock:
            STATE.running = False



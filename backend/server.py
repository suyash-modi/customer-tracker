from __future__ import annotations

"""
FastAPI backend for the browser UI.

Endpoints:
- POST /api/config : set source and start pipeline
- POST /api/line   : set entry/exit line (exactly 2 points)
- GET  /api/meta   : frame width/height (after first frame)
- GET  /api/stream : MJPEG stream of annotated frames
- GET  /api/sessions : sessions snapshot (in-memory)
"""

from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import AppConfig
from backend.app_state import STATE, Zone
from backend.pipeline_runner import start_pipeline_if_needed
from app.video_input import probe_video_source


app = FastAPI(title="Customer Journey Stitching MVP (Local)")

# Dev-friendly CORS so React can call backend from another port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # browser UI is same-origin in this demo but we keep it open for simplicity
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigIn(BaseModel):
    source: str = Field(..., description="YouTube URL OR local path OR webcam index string like '0'")
    device: str = "CPU"
    det_model_xml: str = "models/person-detection-retail-0013.xml"
    reid_model_xml: str = "models/person-reidentification-retail-0287.xml"
    det_conf_threshold: float = 0.55
    reid_cosine_threshold: float = 0.62


class LineIn(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(..., description="List of lines, each with x1, y1, x2, y2")


class ZoneIn(BaseModel):
    name: str = Field(..., description="Name of the zone")
    points: list[dict] = Field(..., description="List of 4 points, each with x and y coordinates")


@app.post("/api/config")
def set_config_and_start(cfg_in: ConfigIn) -> dict:
    # Fail fast if the video source cannot be opened, so the UI shows a clear error
    try:
        probe_video_source(cfg_in.source)
    except RuntimeError as e:
        # RuntimeError messages are already user-friendly, use them directly
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        # For other exceptions, wrap with context
        raise HTTPException(status_code=400, detail=f"Could not open video source: {e}")

    cfg = AppConfig(
        det_model_xml=cfg_in.det_model_xml,
        reid_model_xml=cfg_in.reid_model_xml,
        device=cfg_in.device,
        det_conf_threshold=cfg_in.det_conf_threshold,
        reid_cosine_threshold=cfg_in.reid_cosine_threshold,
    )
    start_pipeline_if_needed(cfg, cfg_in.source)
    return {"ok": True, "config": asdict(cfg)}


@app.post("/api/line")
def set_line(line: LineIn) -> dict:
    """Add a single line (for backward compatibility)."""
    with STATE.lock:
        if STATE.frame_w is None or STATE.frame_h is None:
            raise HTTPException(status_code=400, detail="Frame size unknown yet. Call /api/config first.")
        p1 = (int(line.x1), int(line.y1))
        p2 = (int(line.x2), int(line.y2))
        new_line = (p1, p2)
        if STATE.lines is None:
            STATE.lines = []
        if new_line not in STATE.lines:
            STATE.lines.append(new_line)
        # Also set legacy single line for backward compatibility
        STATE.line_p1 = p1
        STATE.line_p2 = p2
    return {"ok": True, "line": {"p1": p1, "p2": p2}, "all_lines": STATE.lines}


@app.post("/api/lines")
def set_lines(lines_in: LinesIn) -> dict:
    """Set multiple lines at once."""
    with STATE.lock:
        if STATE.frame_w is None or STATE.frame_h is None:
            raise HTTPException(status_code=400, detail="Frame size unknown yet. Call /api/config first.")
        STATE.lines = [((int(l.x1), int(l.y1)), (int(l.x2), int(l.y2))) for l in lines_in.lines]
        # Set first line as legacy single line for backward compatibility
        if STATE.lines:
            STATE.line_p1, STATE.line_p2 = STATE.lines[0]
        else:
            STATE.line_p1 = None
            STATE.line_p2 = None
    return {"ok": True, "lines": STATE.lines}


@app.delete("/api/lines/{line_index}")
def delete_line(line_index: int) -> dict:
    """Delete a specific line by index."""
    with STATE.lock:
        # If there are no lines or index is out of range, treat as a no-op
        if not STATE.lines or line_index < 0 or line_index >= len(STATE.lines):
            # Normalize legacy single line fields
            if not STATE.lines:
                STATE.line_p1 = None
                STATE.line_p2 = None
            return {"ok": True, "lines": STATE.lines or []}

        STATE.lines.pop(line_index)
        # Update legacy single line if needed
        if STATE.lines:
            STATE.line_p1, STATE.line_p2 = STATE.lines[0]
        else:
            STATE.line_p1 = None
            STATE.line_p2 = None
    return {"ok": True, "lines": STATE.lines}


@app.post("/api/zone")
def create_zone(zone_in: ZoneIn) -> dict:
    """Create a new zone with 4 points and a name."""
    with STATE.lock:
        if STATE.frame_w is None or STATE.frame_h is None:
            raise HTTPException(status_code=400, detail="Frame size unknown yet. Call /api/config first.")
        if len(zone_in.points) != 4:
            raise HTTPException(status_code=400, detail="Zone must have exactly 4 points")
        
        # Convert points to tuples
        points = []
        for p in zone_in.points:
            if "x" not in p or "y" not in p:
                raise HTTPException(status_code=400, detail="Each point must have x and y coordinates")
            points.append((int(p["x"]), int(p["y"])))
        
        zone = Zone(name=zone_in.name, points=tuple(points))
        if STATE.zones is None:
            STATE.zones = []
        STATE.zones.append(zone)
        
        return {
            "ok": True,
            "zone": {
                "name": zone.name,
                "points": [{"x": p[0], "y": p[1]} for p in zone.points]
            },
            "all_zones": [{"name": z.name, "points": [{"x": p[0], "y": p[1]} for p in z.points]} for z in STATE.zones]
        }


@app.get("/api/zones")
def get_zones() -> dict:
    """Get all zones."""
    with STATE.lock:
        zones = STATE.get_all_zones()
        return {
            "zones": [{"name": z.name, "points": [{"x": p[0], "y": p[1]} for p in z.points]} for z in zones]
        }


@app.delete("/api/zones/{zone_name}")
def delete_zone(zone_name: str) -> dict:
    """Delete a zone by name."""
    with STATE.lock:
        if STATE.zones is None:
            raise HTTPException(status_code=404, detail=f"Zone '{zone_name}' not found")
        
        # Find and remove zone by name
        found = False
        for i, zone in enumerate(STATE.zones):
            if zone.name == zone_name:
                STATE.zones.pop(i)
                found = True
                break
        
        if not found:
            raise HTTPException(status_code=404, detail=f"Zone '{zone_name}' not found")
        
        return {"ok": True, "zones": [{"name": z.name, "points": [{"x": p[0], "y": p[1]} for p in z.points]} for z in STATE.zones]}


@app.get("/api/meta")
def meta() -> dict:
    with STATE.lock:
        all_lines = STATE.get_all_lines()
        all_zones = STATE.get_all_zones()
        return {
            "frame_w": STATE.frame_w,
            "frame_h": STATE.frame_h,
            "line_p1": STATE.line_p1,
            "line_p2": STATE.line_p2,
            "lines": all_lines if all_lines else [],  # Always return a list, never None
            "zones": [{"name": z.name, "points": [{"x": p[0], "y": p[1]} for p in z.points]} for z in all_zones],
        }


@app.get("/api/sessions")
def sessions() -> dict:
    with STATE.lock:
        return {"sessions": list(STATE.latest_sessions or [])}


def _mjpeg_generator():
    boundary = b"--frame"
    while True:
        with STATE.lock:
            # Wait until a frame is available
            if STATE.latest_jpeg is None:
                STATE.frame_ready.wait(timeout=1.0)
            jpg: Optional[bytes] = STATE.latest_jpeg
        if jpg is None:
            continue
        yield boundary + b"\r\n" + b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"


@app.get("/api/stream")
def stream():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )



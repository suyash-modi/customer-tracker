

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import cv2
import numpy as np

from app.config import AppConfig
from app.line_selector import select_line_on_first_frame
from app.openvino_models import OpenVINOModels
from app.session_manager import SessionManager
from app.tracker import SimpleIoUTracker
from app.video_input import open_video_source
from app.visualizer import Visualizer


@dataclass
class TrackFrameState:
    """Small per-track state used for line-crossing direction inference."""

    last_side: int | None = None
    last_cross_time_s: float = 0.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Customer journey stitching MVP (OpenVINO + IoU tracking + ReID)")
    p.add_argument(
        "--source",
        required=True,
        help='YouTube URL OR local video path OR webcam index (e.g. "0").',
    )
    p.add_argument("--device", default="CPU", help="OpenVINO device, e.g. CPU, GPU, AUTO")
    p.add_argument("--det-model", default="models/person-detection-retail-0013.xml")
    p.add_argument("--reid-model", default="models/person-reidentification-retail-0287.xml")
    p.add_argument("--conf", type=float, default=0.55, help="Detection confidence threshold")
    p.add_argument("--reid-thr", type=float, default=0.62, help="Cosine similarity threshold for stitching")
    p.add_argument("--show-fps", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = AppConfig(
        det_model_xml=args.det_model,
        reid_model_xml=args.reid_model,
        device=args.device,
        det_conf_threshold=args.conf,
        reid_cosine_threshold=args.reid_thr,
    )

    cap = open_video_source(args.source)
    ok, first = cap.read()
    if not ok or first is None:
        raise RuntimeError("Could not read from video source.")

    line_p1, line_p2 = select_line_on_first_frame(first)
    models = OpenVINOModels(cfg)
    tracker = SimpleIoUTracker()
    sessions = SessionManager(reid_cosine_threshold=cfg.reid_cosine_threshold)
    vis = Visualizer(line_p1=line_p1, line_p2=line_p2, show_fps=bool(args.show_fps))

    # Per-DeepSORT-track internal state (for line crossing)
    track_state: dict[int, TrackFrameState] = {}

    prev_ts = time.time()
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break

        # 1) Detect persons
        dets = models.detect_persons(frame, conf_threshold=cfg.det_conf_threshold)

        # 2) ReID embeddings for each detection crop
        embeds = []
        for d in dets:
            crop = frame[d.y1 : d.y2, d.x1 : d.x2]
            embeds.append(models.extract_reid_embedding(crop))
        embeds_np = np.stack(embeds, axis=0) if embeds else np.zeros((0, models.reid_dim), dtype=np.float32)

        # 3) Lightweight IoU tracking (no DeepSORT)
        tracks = tracker.update(dets, embeds_np, frame_shape=frame.shape)

        # 4) For each track, stitch identity + sessions using ReID similarity
        for tr in tracks:
            # Assign a stable "person identity" using our in-memory gallery.
            # We use the latest embedding from the detection matched to this track.
            global_person_id = sessions.assign_identity(
                track_id=tr.track_id,
                embedding=tr.embedding,
            )

            # Line crossing on centroid
            cx = int((tr.x1 + tr.x2) / 2)
            cy = int((tr.y1 + tr.y2) / 2)
            side = vis.side_of_line((cx, cy))

            st = track_state.setdefault(tr.track_id, TrackFrameState(last_side=side))
            event = None
            if st.last_side is not None and side != st.last_side and side != 0 and st.last_side != 0:
                # Direction is defined by sign change relative to the ordered points (p1 -> p2).
                # -1 -> +1 is considered direction A (ENTRY)
                # +1 -> -1 is considered direction B (EXIT)
                if st.last_side < side:
                    event = "ENTRY"
                else:
                    event = "EXIT"

                # Debounce rapid flips around the line
                now = time.time()
                if now - st.last_cross_time_s < 0.75:
                    event = None
                else:
                    st.last_cross_time_s = now

            st.last_side = side

            if event == "ENTRY":
                sessions.on_entry(global_person_id)
            elif event == "EXIT":
                sessions.on_exit(global_person_id)

            tr.global_person_id = global_person_id
            tr.session_id = sessions.get_session_id(global_person_id)
            tr.cross_event = event

        # 5) Draw overlay
        now = time.time()
        fps = 1.0 / max(1e-6, (now - prev_ts))
        prev_ts = now
        out = vis.draw(frame, tracks, sessions.active_sessions(), fps=fps)

        cv2.imshow("Customer Journey Stitching MVP", out)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

    # Print sessions summary to console (educational visibility).
    print("\n=== Sessions ===")
    for s in sessions.all_sessions():
        print(s)


if __name__ == "__main__":
    main()



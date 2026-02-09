from __future__ import annotations

"""
OpenVINO model wrappers (MANDATORY models):
- person-detection-retail-0013
- person-reidentification-retail-0287

Kept as a thin wrapper so you can easily swap devices or models later.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from openvino.runtime import Core, CompiledModel, Model

from app.config import AppConfig
from app.types import Detection
from app.utils import clamp, l2_normalize


@dataclass(frozen=True)
class _OVModelIO:
    compiled: CompiledModel
    input_name: str
    output_name: str
    input_shape: tuple


class OpenVINOModels:
    def __init__(self, cfg: AppConfig):
        core = Core()
        det_m: Model = core.read_model(cfg.det_model_xml)
        reid_m: Model = core.read_model(cfg.reid_model_xml)

        det_c = core.compile_model(det_m, cfg.device)
        reid_c = core.compile_model(reid_m, cfg.device)

        self.det = self._wrap(det_c)
        self.reid = self._wrap(reid_c)

        # ReID output dimension (depends on model; 0287 is commonly 256)
        dummy = np.zeros(self.reid.input_shape, dtype=np.float32)
        out = self.reid.compiled([dummy])[self.reid.output_name]
        self.reid_dim = int(np.prod(out.shape[1:]))

    @staticmethod
    def _wrap(compiled: CompiledModel) -> _OVModelIO:
        inp = compiled.inputs[0]
        out = compiled.outputs[0]
        return _OVModelIO(
            compiled=compiled,
            input_name=inp.get_any_name(),
            output_name=out.get_any_name(),
            input_shape=tuple(inp.shape),
        )

    def detect_persons(self, frame_bgr: np.ndarray, conf_threshold: float) -> list[Detection]:
        """
        person-detection-retail-0013 output: [1,1,N,7] with normalized coords.
        """
        h, w = frame_bgr.shape[:2]
        n, c, ih, iw = self.det.input_shape  # NCHW

        resized = cv2.resize(frame_bgr, (iw, ih))
        blob = resized.transpose(2, 0, 1)[None, ...].astype(np.float32)  # 1x3xH xW

        raw = self.det.compiled([blob])[self.det.output_name]
        raw = np.asarray(raw)

        dets: list[Detection] = []
        for i in range(raw.shape[2]):
            _image_id, label, conf, x_min, y_min, x_max, y_max = raw[0, 0, i]
            if conf < conf_threshold:
                continue
            if int(label) != 1:
                continue
            x1 = clamp(int(x_min * w), 0, w - 1)
            y1 = clamp(int(y_min * h), 0, h - 1)
            x2 = clamp(int(x_max * w), 0, w - 1)
            y2 = clamp(int(y_max * h), 0, h - 1)
            if x2 <= x1 or y2 <= y1:
                continue
            dets.append(Detection(x1=x1, y1=y1, x2=x2, y2=y2, conf=float(conf)))
        return dets

    def extract_reid_embedding(self, crop_bgr: np.ndarray) -> np.ndarray:
        """
        person-reidentification-retail-0287 typically expects 1x3x128x64 (NCHW).
        We resize any crop to model input size, run inference, then L2-normalize.
        """
        n, c, ih, iw = self.reid.input_shape  # NCHW
        if crop_bgr is None or crop_bgr.size == 0:
            # Return a stable zero vector (won't match anything)
            return np.zeros((self.reid_dim,), dtype=np.float32)

        resized = cv2.resize(crop_bgr, (iw, ih))
        blob = resized.transpose(2, 0, 1)[None, ...].astype(np.float32)

        out = self.reid.compiled([blob])[self.reid.output_name]
        vec = np.asarray(out).reshape(-1).astype(np.float32)
        vec = l2_normalize(vec)
        return vec



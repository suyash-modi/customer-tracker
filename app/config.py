from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """
    Centralized configuration for the demo.

    Keep config small and explicit so the pipeline is easy to debug.
    """

    det_model_xml: str
    reid_model_xml: str
    device: str = "CPU"

    det_conf_threshold: float = 0.55
    reid_cosine_threshold: float = 0.62



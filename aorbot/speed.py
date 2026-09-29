"""Target speed along the line: corner speed from curvature and grip, then a
backward pass so the car starts braking early enough for every corner."""

from __future__ import annotations

import numpy as np

from .config import SpeedConfig
from .line import RacingLine


def corner_speed(kappa: np.ndarray, grip: float) -> np.ndarray:
    """Highest speed with |v^2 * kappa| <= grip."""
    return np.sqrt(grip / np.maximum(np.abs(kappa), 1e-6))


def braking_pass(v: np.ndarray, ds: np.ndarray, decel: float) -> np.ndarray:
    """Cap every point so the next one is reachable with ``decel`` braking."""
    out = np.array(v, float)
    for i in range(len(out) - 2, -1, -1):
        reach = np.sqrt(out[i + 1] ** 2 + 2.0 * decel * ds[i])
        if out[i] > reach:
            out[i] = reach
    return out


def speed_profile(line: RacingLine, cfg: SpeedConfig, scale: np.ndarray | None = None) -> np.ndarray:
    """Target speed per line point. ``scale`` (per point) multiplies the corner
    speed caps; it is what the learner adjusts between attempts."""
    v = np.minimum(corner_speed(line.kappa, cfg.grip), cfg.v_max)
    if scale is not None:
        v = v * scale
    v = np.clip(v, cfg.v_min, cfg.v_max)
    return braking_pass(v, np.diff(line.s), cfg.brake_decel)


def estimate_grip(line: RacingLine, xy: np.ndarray, speed: np.ndarray, percentile: float = 90.0) -> float:
    """Lateral acceleration a demonstration actually used (v^2 * kappa of the
    line at each sample): a first guess of what this car + weather can do."""
    a = []
    s = None
    for (x, y), v in zip(np.asarray(xy, float), np.asarray(speed, float)):
        p = line.project(x, y, s_hint=s)
        s = p.s
        if v > 3.0:
            a.append(v * v * abs(line.curvature_at(p.s)))
    if not a:
        raise ValueError("demonstration never moved faster than 3 m/s")
    return float(np.percentile(a, percentile))

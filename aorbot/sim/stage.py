"""Procedural rally stages for offline testing: straights, sweepers, corners
and hairpins, with a road width and a verge (trees beyond it)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..line import RacingLine


@dataclass
class SimStage:
    centerline: RacingLine
    half_width: float  # m of road either side of the centerline
    verge: float  # m of rough ground beyond the road before the trees
    seed: int

    @property
    def crash_offset(self) -> float:
        return self.half_width + self.verge


def _segment(rng: np.random.Generator) -> list[tuple[float, float]]:
    """(length, curvature) pieces for one stage feature, sampled every metre."""
    kind = rng.choice(["straight", "sweeper", "corner", "hairpin"], p=[0.3, 0.3, 0.3, 0.1])
    sign = rng.choice([-1.0, 1.0])
    if kind == "straight":
        return [(rng.uniform(40, 180), 0.0)]
    if kind == "sweeper":
        radius, angle = rng.uniform(70, 200), math.radians(rng.uniform(20, 70))
    elif kind == "corner":
        radius, angle = rng.uniform(18, 45), math.radians(rng.uniform(45, 110))
    else:
        radius, angle = rng.uniform(10, 14), math.radians(rng.uniform(150, 180))
    return [(rng.uniform(10, 30), 0.0), (radius * angle, sign / radius)]


def _integrate(start: np.ndarray, heading: float, pieces) -> tuple[np.ndarray, float]:
    pts, h, p = [], heading, start.copy()
    for length, kappa in pieces:
        for _ in range(max(1, int(round(length)))):
            h += kappa * 1.0
            p = p + np.array([math.cos(h), math.sin(h)])
            pts.append(p)
    return np.array(pts), h


def make_stage(seed: int = 7, length: float = 2000.0, half_width: float = 4.0, verge: float = 2.5) -> SimStage:
    rng = np.random.default_rng(seed)
    clearance = 2 * (half_width + verge) + 6.0
    pts = [np.zeros(2)]
    heading = 0.0
    # Start and finish with a straight, like a real stage.
    seg, heading = _integrate(pts[-1], heading, [(60.0, 0.0)])
    pts.extend(seg)
    while len(pts) < length - 50:
        for _attempt in range(30):
            seg, h_new = _integrate(pts[-1], heading, _segment(rng))
            old = np.array(pts[:-80:4]) if len(pts) > 80 else np.zeros((0, 2))
            if len(old) == 0 or np.min(np.hypot(*(seg[::4, None, :] - old[None, :, :]).transpose(2, 0, 1))) > clearance:
                pts.extend(seg)
                heading = h_new
                break
        else:  # boxed in: finish the stage here
            break
    seg, heading = _integrate(pts[-1], heading, [(50.0, 0.0)])
    pts.extend(seg)
    return SimStage(RacingLine(np.array(pts), spacing=2.0), half_width, verge, seed)

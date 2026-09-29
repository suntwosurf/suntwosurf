"""Reference line from the game's own stage path (no recorded run needed).

The plugin writes every point list in the scene that forms a long path past
the car (``BepInEx/aorbot/paths_latest.json``). Here each candidate is turned
to face the way the car points, cut to start at the car, and checked: a road
path passes right by the car, points along it, has no big jumps or kinks.
The best one becomes the reference line.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .line import RacingLine, drop_duplicates, resample_polyline

MAX_CAR_DISTANCE = 30.0  # m from the car to the path
MAX_ANGLE = 45.0  # deg between the path and the car's forward direction
MIN_LENGTH = 300.0  # m of path ahead of the car
MAX_GAP = 80.0  # m between consecutive points
MAX_TURN = 100.0  # deg between consecutive segments


@dataclass
class PathCandidate:
    index: int
    source: str
    points: np.ndarray  # (N, 2) ground-plane points from the car onwards
    car_distance: float = math.inf
    angle: float = math.inf
    length: float = 0.0
    max_gap: float = math.inf
    max_turn: float = math.inf
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _nearest(points: np.ndarray, p: np.ndarray) -> tuple[int, float, np.ndarray]:
    a, b = points[:-1], points[1:]
    ab = b - a
    len2 = np.maximum((ab**2).sum(axis=1), 1e-9)
    t = np.clip(((p - a) * ab).sum(axis=1) / len2, 0.0, 1.0)
    q = a + t[:, None] * ab
    d = np.hypot(*(q - p).T)
    i = int(np.argmin(d))
    return i, float(d[i]), q[i]


def analyse(index: int, source: str, raw_points, car: np.ndarray, forward: np.ndarray) -> PathCandidate:
    pts = np.asarray(raw_points, float)
    pts = drop_duplicates(pts[:, [0, 2]], 0.05)  # Unity (x, z) = our ground plane
    cand = PathCandidate(index, source, pts)
    if len(pts) < 3:
        cand.problems.append("too few points")
        return cand
    i, dist, q = _nearest(pts, car)
    seg = pts[i + 1] - pts[i]
    if float(seg @ forward) < 0:  # path runs the other way: turn it around
        pts = pts[::-1].copy()
        i, dist, q = _nearest(pts, car)
        seg = pts[i + 1] - pts[i]
    pts = np.vstack([q, pts[i + 1 :]])
    pts = drop_duplicates(pts, 0.05)
    cand.points = pts
    cand.car_distance = dist
    cos = float(seg @ forward) / max(np.linalg.norm(seg) * np.linalg.norm(forward), 1e-9)
    cand.angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
    if len(pts) >= 2:
        steps = np.diff(pts, axis=0)
        gaps = np.hypot(*steps.T)
        cand.length = float(gaps.sum())
        cand.max_gap = float(gaps.max())
        heading = np.arctan2(steps[:, 1], steps[:, 0])
        turns = np.abs((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi)
        cand.max_turn = math.degrees(float(turns.max())) if len(turns) else 0.0
    for bad, text in (
        (cand.car_distance > MAX_CAR_DISTANCE, f"{cand.car_distance:.0f} m from the car"),
        (cand.angle > MAX_ANGLE, f"{cand.angle:.0f} deg off the car's direction"),
        (cand.length < MIN_LENGTH, f"only {cand.length:.0f} m ahead"),
        (cand.max_gap > MAX_GAP, f"jumps of {cand.max_gap:.0f} m"),
        (cand.max_turn > MAX_TURN, f"kinks of {cand.max_turn:.0f} deg"),
    ):
        if bad:
            cand.problems.append(text)
    return cand


def load_candidates(dump_path: str | Path) -> tuple[dict, list[PathCandidate]]:
    dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
    car = np.array([dump["car"][0], dump["car"][2]], float)
    forward = np.array([dump["car_forward"][0], dump["car_forward"][2]], float)
    cands = [analyse(i, c["source"], c["points"], car, forward) for i, c in enumerate(dump["candidates"])]
    return dump, cands


def pick(cands: list[PathCandidate]) -> PathCandidate | None:
    """Among usable paths nearly as long as the longest, the one closest to
    the car (the road centre rather than a fence or tree line beside it)."""
    good = [c for c in cands if c.ok]
    if not good:
        return None
    longest = max(c.length for c in good)
    return min((c for c in good if c.length >= 0.6 * longest), key=lambda c: c.car_distance)


def catmull_rom(points: np.ndarray, step: float = 1.0) -> np.ndarray:
    """Smooth curve through sparse control points."""
    p = np.vstack([2 * points[0] - points[1], points, 2 * points[-1] - points[-2]])
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        n = max(2, int(math.ceil(np.linalg.norm(p2 - p1) / step)))
        for t in np.linspace(0.0, 1.0, n, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(points[-1])
    return np.asarray(out)


def line_from_candidate(cand: PathCandidate, scene: str = "") -> RacingLine:
    pts = cand.points
    spacing = cand.length / max(len(pts) - 1, 1)
    fine = catmull_rom(pts) if spacing > 4.0 else resample_polyline(pts, 1.0)
    line = RacingLine(fine, spacing=2.0)
    line.meta = {"source": f"game path: {cand.source}", "scene": scene}
    return line

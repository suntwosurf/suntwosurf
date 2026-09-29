"""Learning from one demonstration: record a human run through the stage and
turn it into the reference line (plus a grip estimate for this car/weather)."""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from .line import RacingLine
from .speed import estimate_grip
from .types import CarState

TRACE_FIELDS = ("t", "x", "y", "heading", "speed")


def states_to_trace(states: list[CarState]) -> dict[str, np.ndarray]:
    return {k: np.array([getattr(s, k) for s in states], float) for k in TRACE_FIELDS}


def save_trace(path: str | Path, trace: dict[str, np.ndarray]) -> None:
    with open(path, "wb") as fh:
        np.savez(fh, **trace)


def load_trace(path: str | Path) -> dict[str, np.ndarray]:
    with np.load(path) as data:
        return {k: data[k] for k in data.files}


def record_run(receiver, idle_stop: float = 5.0, min_distance: float = 50.0, max_time: float = 1800.0,
               log=print) -> list[CarState]:
    """Collect telemetry while a human drives. Stops once the car has covered
    ``min_distance`` and then stands still for ``idle_stop`` s (after the
    finish line), or on Ctrl+C."""
    states: list[CarState] = []
    distance = 0.0
    still_since = None
    start = time.monotonic()
    last_log = 0.0
    try:
        while time.monotonic() - start < max_time:
            st = receiver.wait_fresh(1.0)
            if st is None:
                continue
            if not st.race_on:
                still_since = None
                continue
            if states:
                distance += math.hypot(st.x - states[-1].x, st.y - states[-1].y)
            states.append(st)
            if distance > min_distance and st.speed < 0.5:
                still_since = still_since or st.t
                if st.t - still_since > idle_stop:
                    break
            else:
                still_since = None
            if st.t - last_log > 2.0:
                last_log = st.t
                log(f"  recording: {len(states)} samples, {distance:7.1f} m, {st.speed * 3.6:5.1f} km/h")
    except KeyboardInterrupt:
        log("  stopped by Ctrl+C")
    return states


def build_line(trace: dict[str, np.ndarray], spacing: float = 2.0, smooth: float = 8.0,
               trim_end: float = 0.0) -> tuple[RacingLine, float]:
    """Reference line from a recorded trace, and the grip the driver used."""
    xy = np.column_stack([trace["x"], trace["y"]])
    moving = trace["speed"] > 0.5
    if moving.sum() < 10:
        raise ValueError("the recording never moved")
    first, last = np.flatnonzero(moving)[[0, -1]]
    line = RacingLine.from_trace(xy[first : last + 1], spacing=spacing, smooth=smooth)
    if trim_end > 0:
        keep = line.s <= line.length - trim_end
        line = RacingLine(line.xy[keep], spacing=None)
    grip = estimate_grip(line, xy[first : last + 1], trace["speed"][first : last + 1])
    line.meta = {"grip_estimate": round(grip, 3), "demo_time": float(trace["t"][last] - trace["t"][first])}
    return line, grip

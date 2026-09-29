"""One attempt at the stage: reset, drive until finished or failed, and keep a
trace of everything the learner needs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

import numpy as np

from .config import RulesConfig
from .controller import LineFollower
from .line import RacingLine
from .types import Action, CarState


class VehicleIO(Protocol):
    """What the bot needs from the game (or from the simulator)."""

    dt: float

    def reset(self) -> CarState:
        """Restart the stage and return once the car is ready at the start."""

    def step(self, action: Action) -> CarState:
        """Apply ``action`` for one control period and return the new state."""

    def release(self) -> None:
        """Let go of all controls."""


class Status(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    OFF_STAGE = "off_stage"
    STUCK = "stuck"
    FLIPPED = "flipped"
    TIMEOUT = "timeout"


class StageTracker:
    """Progress along the reference line and the finish/fail rules."""

    def __init__(self, line: RacingLine, rules: RulesConfig):
        self.line = line
        self.rules = rules

    def reset(self, state: CarState) -> None:
        self.t0 = state.t
        self.s = self.line.project(state.x, state.y).s
        self.s_max = self.s
        self.slow_since: float | None = None
        self.flip_since: float | None = None
        self.zone_since: float | None = None  # entered the finish zone
        self.finish_t: float | None = None

    def update(self, state: CarState) -> tuple[float, float, Status]:
        r = self.rules
        p = self.line.project(state.x, state.y, s_hint=self.s, window=r.search_window)
        self.s = p.s
        self.s_max = max(self.s_max, p.s)
        elapsed = state.t - self.t0
        if r.finish_zone > 0 and self.zone_since is None and p.s >= self.line.length - r.finish_zone:
            self.zone_since = state.t

        if abs(p.offset) > r.max_offset:
            return p.s, p.offset, Status.OFF_STAGE
        if p.s >= self.line.length - r.finish_margin:
            self.finish_t = state.t
            return p.s, p.offset, Status.FINISHED
        if state.upright < r.min_upright:
            self.flip_since = state.t if self.flip_since is None else self.flip_since
            if state.t - self.flip_since >= r.flip_time:
                return p.s, p.offset, Status.FLIPPED
        else:
            self.flip_since = None
        if elapsed > r.start_grace and state.speed < r.stuck_speed:
            self.slow_since = state.t if self.slow_since is None else self.slow_since
            if state.t - self.slow_since >= r.stuck_time:
                if self.zone_since is not None:  # the game stopped the car after the finish
                    self.finish_t = self.zone_since
                    return p.s, p.offset, Status.FINISHED
                return p.s, p.offset, Status.STUCK
        else:
            self.slow_since = None
        if elapsed > r.timeout:
            return p.s, p.offset, Status.TIMEOUT
        return p.s, p.offset, Status.RUNNING


TRACE_KEYS = ("t", "x", "y", "s", "offset", "speed", "v_target", "steer", "throttle", "brake")


@dataclass
class EpisodeResult:
    status: Status
    time: float  # s from the start to the end of the attempt
    progress: float  # furthest point reached along the line, m
    end_s: float  # progress where the attempt ended (the failure point)
    trace: dict[str, np.ndarray] = field(repr=False, default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.status is Status.FINISHED


def run_episode(
    io: VehicleIO,
    driver: LineFollower,
    tracker: StageTracker,
    on_step: Callable[[CarState, float, float, Action], None] | None = None,
) -> EpisodeResult:
    state = io.reset()
    tracker.reset(state)
    rows: dict[str, list[float]] = {k: [] for k in TRACE_KEYS}
    try:
        while True:
            s, offset, status = tracker.update(state)
            if status is not Status.RUNNING:
                break
            action, info = driver.act(state, s)
            for k, v in zip(
                TRACE_KEYS,
                (state.t, state.x, state.y, s, offset, state.speed, info["v_target"],
                 action.steer, action.throttle, action.brake),
            ):
                rows[k].append(v)
            if on_step is not None:
                on_step(state, s, offset, action)
            state = io.step(action)
    finally:
        io.release()
    end_t = tracker.finish_t if status is Status.FINISHED else state.t
    return EpisodeResult(
        status=status,
        time=end_t - tracker.t0,
        progress=tracker.s_max,
        end_s=s,
        trace={k: np.asarray(v, float) for k, v in rows.items()},
    )

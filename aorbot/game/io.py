"""The real game as a VehicleIO: telemetry in, controls out, restart macro."""

from __future__ import annotations

import math
import time

from ..config import ControlsConfig, ResetConfig, TelemetryConfig
from ..types import Action, CarState
from .controls import ControlBackend
from .telemetry import TelemetryReceiver


class TelemetryLost(RuntimeError):
    pass


class ResetFailed(RuntimeError):
    pass


def run_macro(controls: ControlBackend, macro: list[str], sleep=time.sleep) -> None:
    """Steps are button/key names, or "wait <seconds>"."""
    for step in macro:
        parts = step.split()
        if parts and parts[0].lower() == "wait":
            sleep(float(parts[1]))
        else:
            controls.tap(step)


class GameIO:
    def __init__(
        self,
        receiver: TelemetryReceiver,
        controls: ControlBackend,
        start_xy: tuple[float, float],
        controls_cfg: ControlsConfig,
        reset_cfg: ResetConfig,
        telemetry_cfg: TelemetryConfig,
        log=print,
    ):
        self.receiver = receiver
        self.controls = controls
        self.start_xy = start_xy
        self.reset_cfg = reset_cfg
        self.telemetry_cfg = telemetry_cfg
        self.dt = 1.0 / controls_cfg.control_hz
        self.log = log
        self._next = time.monotonic()

    def at_start(self, state: CarState | None) -> bool:
        if state is None or not state.race_on:
            return False
        d = math.hypot(state.x - self.start_xy[0], state.y - self.start_xy[1])
        return d < self.reset_cfg.start_radius and state.speed < 0.5

    def reset(self) -> CarState:
        cfg = self.reset_cfg
        self.controls.neutral()
        run_macro(self.controls, cfg.macro)
        deadline = time.monotonic() + cfg.timeout
        since = None
        while time.monotonic() < deadline:
            state = self.receiver.wait_fresh(0.5)
            if self.at_start(state):
                since = since or time.monotonic()
                if time.monotonic() - since >= cfg.settle_time:
                    break
            else:
                since = None
        else:
            state, age = self.receiver.latest()
            where = "no telemetry" if state is None else f"car at ({state.x:.1f}, {state.y:.1f}), {age:.1f}s old"
            raise ResetFailed(f"car did not come to rest at the stage start within {cfg.timeout}s ({where})")
        time.sleep(cfg.countdown)
        self._next = time.monotonic()
        return self._fresh()

    def _fresh(self) -> CarState:
        state, age = self.receiver.latest()
        if state is None or age > self.telemetry_cfg.timeout:
            raise TelemetryLost(f"no telemetry for {age:.1f}s")
        return state

    def step(self, action: Action) -> CarState:
        self.controls.send(action)
        self._next += self.dt
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:  # fell behind (slow machine / hitch): resync instead of bursting
            self._next = time.monotonic()
        return self._fresh()

    def release(self) -> None:
        self.controls.neutral()

    def close(self) -> None:
        self.controls.close()
        self.receiver.close()

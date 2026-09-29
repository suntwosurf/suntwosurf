"""Driving policy: pure-pursuit steering toward a point ahead on the line and
proportional throttle/brake toward the target speed profile."""

from __future__ import annotations

import math

import numpy as np

from .config import ControllerConfig
from .line import RacingLine
from .types import Action, CarState


class LineFollower:
    def __init__(
        self,
        line: RacingLine,
        cfg: ControllerConfig,
        v_target: np.ndarray,
        lateral: np.ndarray | None = None,
        grip: float | None = None,
    ):
        self.line = line
        self.cfg = cfg
        self.grip = grip  # m/s^2; enables traction-aware throttle
        self.set_targets(v_target, lateral)

    def set_targets(self, v_target: np.ndarray, lateral: np.ndarray | None = None) -> None:
        """``v_target`` and ``lateral`` (shift of the aim point to the left, m)
        are given per line point."""
        self.v_target = np.asarray(v_target, float)
        self.lateral = None if lateral is None else np.asarray(lateral, float)
        if len(self.v_target) != len(self.line.s):
            raise ValueError("v_target must have one value per line point")

    def target_speed(self, s: float) -> float:
        return float(np.interp(s, self.line.s, self.v_target))

    def act(self, state: CarState, s: float) -> tuple[Action, dict]:
        cfg, line = self.cfg, self.line
        v = max(state.speed, 0.0)

        lookahead = min(max(cfg.lookahead_base + cfg.lookahead_time * v, cfg.lookahead_min), cfg.lookahead_max)
        s_aim = min(s + lookahead, line.length)
        aim = line.point_at(s_aim)
        if self.lateral is not None:
            aim = aim + line.normal_at(s_aim) * float(np.interp(s_aim, line.s, self.lateral))
        dx, dy = aim[0] - state.x, aim[1] - state.y
        dist = max(math.hypot(dx, dy), 1.0)
        alpha = math.atan2(dy, dx) - state.heading
        alpha = (alpha + math.pi) % (2 * math.pi) - math.pi
        curvature = 2.0 * math.sin(alpha) / dist  # + = turn left
        wheel = math.atan(cfg.wheelbase * curvature) * cfg.steer_gain
        steer = -wheel / cfg.max_steer_angle  # stick: + = right

        v_t = self.target_speed(min(s + v * cfg.speed_preview, line.length))
        err = v_t - v
        if err > -cfg.brake_deadband:
            throttle, brake = cfg.cruise_throttle + cfg.throttle_kp * err, 0.0
            if self.grip and cfg.traction_limit:
                # Friction circle: grip used for turning is not available for
                # accelerating, so ease off the throttle while cornering hard.
                lateral_use = min(1.0, v * v * abs(curvature) / self.grip)
                throttle = min(throttle, max(cfg.min_corner_throttle, math.sqrt(1.0 - lateral_use**2)))
        else:
            throttle, brake = 0.0, cfg.brake_kp * (-err - cfg.brake_deadband) + 0.1

        action = Action(steer=steer, throttle=throttle, brake=brake).clipped()
        return action, {"v_target": v_t, "lookahead": lookahead}

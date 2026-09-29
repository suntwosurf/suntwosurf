"""A deliberately simple rally car: kinematic bicycle limited by a friction
circle. Entering a corner too fast makes it run wide (understeer), which is
the failure mode the learner has to discover. It is a stand-in for testing
the pipeline, not a model of art of rally's physics."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..types import Action

G = 9.81

# Grip multiplier per weather, on top of the base gravel grip.
WEATHER_GRIP = {"dry": 1.0, "clear": 1.0, "fog": 1.0, "night": 1.0, "wet": 0.78, "rain": 0.78, "snow": 0.55}


@dataclass
class SimCarParams:
    wheelbase: float = 2.5
    max_steer: float = 0.55  # rad at full stick
    steer_rate: float = 3.0  # rad/s steering actuator speed
    mu: float = 0.85  # gravel
    engine_accel: float = 6.0  # m/s^2 traction-limited launch
    power_per_mass: float = 110.0  # W/kg
    drag: float = 0.0006  # 1/m
    rolling: float = 0.15  # m/s^2
    brake_efficiency: float = 0.9
    scrub: float = 0.35  # extra deceleration share when sliding wide
    offroad_grip: float = 0.6
    offroad_drag: float = 2.0  # m/s^2


class SimCar:
    def __init__(self, params: SimCarParams | None = None, weather: str = "dry"):
        self.p = params or SimCarParams()
        if weather not in WEATHER_GRIP:
            raise ValueError(f"unknown weather {weather!r}; one of {', '.join(WEATHER_GRIP)}")
        self.grip_factor = WEATHER_GRIP[weather]
        self.place(0.0, 0.0, 0.0)

    def place(self, x: float, y: float, heading: float) -> None:
        self.x, self.y, self.heading = x, y, heading
        self.v = 0.0
        self.wheel = 0.0
        self.yaw_rate = 0.0

    def step(self, action: Action, dt: float, offroad: bool = False) -> None:
        p = self.p
        mu = p.mu * self.grip_factor * (p.offroad_grip if offroad else 1.0)
        target = -action.steer * p.max_steer
        self.wheel += max(-p.steer_rate * dt, min(p.steer_rate * dt, target - self.wheel))

        v = self.v
        drive = action.throttle * min(p.engine_accel, p.power_per_mass / max(v, 1.0))
        brake = action.brake * mu * G * p.brake_efficiency + (0.5 * mu * G if action.handbrake else 0.0)
        tyre_long = min(drive, mu * G) - brake
        # Friction circle: what longitudinal force uses is not available sideways.
        lateral_grip = math.sqrt(max((mu * G) ** 2 - tyre_long**2, (0.25 * mu * G) ** 2))
        k_wanted = math.tan(self.wheel) / p.wheelbase
        k_max = lateral_grip / max(v * v, 0.01)
        k = max(-k_max, min(k_max, k_wanted))
        accel = tyre_long - p.drag * v * v - p.rolling - (p.offroad_drag if offroad else 0.0)
        if abs(k_wanted) > k_max:  # sliding wide scrubs speed
            accel -= p.scrub * mu * G * (1.0 - k_max / abs(k_wanted))

        self.v = max(0.0, v + accel * dt)
        self.yaw_rate = self.v * k
        self.heading += self.yaw_rate * dt
        self.x += self.v * math.cos(self.heading) * dt
        self.y += self.v * math.sin(self.heading) * dt

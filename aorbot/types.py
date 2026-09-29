"""Plain data types shared by the simulator, the game bridge and the learner."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CarState:
    """Car state projected onto the ground plane.

    Frame convention: ``x`` is Unity X and ``y`` is Unity Z, so seen from above
    angles are counter-clockwise (left turn = positive yaw rate / curvature).
    """

    t: float  # seconds on the game (or simulator) clock
    x: float
    y: float
    heading: float  # rad, counter-clockwise from +x
    speed: float  # m/s, horizontal
    vx: float = 0.0  # world-frame horizontal velocity
    vy: float = 0.0
    yaw_rate: float = 0.0  # rad/s, counter-clockwise positive
    upright: float = 1.0  # dot(car up, world up); near 0 or negative = on its side/roof
    race_on: bool = True  # False while paused / loading, when the source knows


@dataclass
class Action:
    """One control command, in game-pad terms."""

    steer: float = 0.0  # -1 full left .. +1 full right
    throttle: float = 0.0  # 0..1
    brake: float = 0.0  # 0..1
    handbrake: bool = False

    def clipped(self) -> "Action":
        return Action(
            steer=min(1.0, max(-1.0, float(self.steer))),
            throttle=min(1.0, max(0.0, float(self.throttle))),
            brake=min(1.0, max(0.0, float(self.brake))),
            handbrake=bool(self.handbrake),
        )


@dataclass(frozen=True)
class Conditions:
    """The fixed setup the bot learns for. A learned profile is only valid for
    exactly this stage + car + weather, so it is stored with the learner state
    and checked on load."""

    stage: str
    car: str
    weather: str

    def label(self) -> str:
        return f"{self.stage} | {self.car} | {self.weather}"

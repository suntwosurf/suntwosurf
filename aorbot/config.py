"""Configuration: one TOML file describes the fixed stage/car/weather and how
to talk to the game. Every section maps onto a dataclass below; unknown keys
are rejected so typos do not silently fall back to defaults."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .types import Conditions


@dataclass
class ControllerConfig:
    """Pure-pursuit steering + proportional speed control."""

    wheelbase: float = 2.5  # m
    max_steer_angle: float = 0.55  # rad of wheel angle that full stick maps to
    steer_gain: float = 1.0
    lookahead_base: float = 5.0  # m
    lookahead_time: float = 0.45  # s; lookahead grows with speed
    lookahead_min: float = 5.0
    lookahead_max: float = 30.0
    speed_preview: float = 0.3  # s; read the target speed slightly ahead to hide lag
    cruise_throttle: float = 0.35  # throttle when on target speed
    throttle_kp: float = 0.25  # throttle per m/s below target
    brake_kp: float = 0.15  # brake per m/s above target (beyond the deadband)
    brake_deadband: float = 1.0  # m/s
    traction_limit: bool = True  # less throttle while using grip to turn
    min_corner_throttle: float = 0.15


@dataclass
class SpeedConfig:
    """Physics-based target speed profile, before the learned per-bin scales."""

    # Usable lateral acceleration, m/s^2. 0 = use the estimate `aorbot build-line`
    # stored in the line file (what your demonstration run used).
    grip: float = 0.0
    brake_decel: float = 6.0  # m/s^2 assumed when planning braking (lower it for wet/snow)
    v_max: float = 50.0  # m/s
    v_min: float = 4.0  # m/s


@dataclass
class RulesConfig:
    """When an attempt counts as finished or failed."""

    max_offset: float = 6.0  # m away from the reference line = off the stage
    stuck_speed: float = 1.0  # m/s
    stuck_time: float = 4.0  # s below stuck_speed (after the start grace) = stuck
    start_grace: float = 6.0  # s after the start before 'stuck' can trigger
    min_upright: float = 0.3  # below this for flip_time = flipped
    flip_time: float = 1.0
    timeout: float = 600.0  # s
    finish_margin: float = 5.0  # m before the end of the line counts as finished
    # If the game takes control after the finish line, the car stops before the
    # end of your recorded line: coming to a stop within this many metres of
    # the end counts as finished (timed from entering the zone). 0 = off.
    finish_zone: float = 0.0
    search_window: float = 60.0  # m around the last progress used to project


@dataclass
class LearnConfig:
    """Iterative learning of the speed profile across repeated attempts."""

    bin_length: float = 20.0  # m of stage per learned speed scale
    init_scale: float = 0.7  # start cautious
    min_scale: float = 0.25
    max_scale: float = 1.6
    grow: float = 1.05  # clean bins get faster ...
    limit_margin: float = 0.95  # ... up to this share of the scale they once failed at
    warn_frac: float = 0.6  # |offset| above this share of max_offset = near miss
    warn_decay: float = 0.97
    warn_limit: float = 1.08  # a near miss puts the bin's limit this far above its scale
    warn_back: int = 2  # near miss also slows the bins leading into it
    calm_frac: float = 0.35  # |offset| below this share = clean, may grow
    fail_decay: float = 0.85
    fail_lookback: float = 60.0  # m before a failure that get slowed down
    lateral_gain: float = 0.0  # >0 enables the lateral (steering) learning term
    max_lateral_shift: float = 1.5  # m


@dataclass
class TelemetryConfig:
    format: str = "aorbot"  # "aorbot" (mod/ plugin) or "forza" (FH4/FH5 dash packets)
    host: str = "127.0.0.1"
    port: int = 47800
    timeout: float = 1.0  # s without packets during a run = error
    # Only for format = "forza": how the packet's yaw maps to our heading.
    # heading = yaw_sign * yaw + yaw_offset_deg (in rad); defaults assume
    # Unity euler Y (clockwise from +Z). `aorbot check-telemetry` shows whether
    # it matches the direction of travel.
    yaw_sign: float = -1.0
    yaw_offset_deg: float = 90.0


@dataclass
class ControlsConfig:
    backend: str = "gamepad"  # gamepad | keyboard | udp | none
    control_hz: float = 30.0
    steer_deadzone: float = 0.0  # compensate the game's stick deadzone (0..0.5)
    handbrake_button: str = "B"  # gamepad button name bound to handbrake in-game
    # keyboard backend (in-game key bindings)
    key_left: str = "left"
    key_right: str = "right"
    key_throttle: str = "up"
    key_brake: str = "down"
    key_handbrake: str = "space"
    # udp backend (fake-game loopback)
    udp_host: str = "127.0.0.1"
    udp_port: int = 47801


@dataclass
class ResetConfig:
    """How to restart the stage between attempts: a macro of button taps
    ("A", "DPAD_DOWN", ... for the gamepad; key names for the keyboard) and
    "wait <seconds>" steps. `aorbot test-reset` runs it once."""

    macro: list[str] = field(
        default_factory=lambda: ["START", "wait 1.0", "DPAD_DOWN", "wait 0.4", "A", "wait 0.8", "A"]
    )
    start_radius: float = 15.0  # m from the line start that counts as "at the start"
    settle_time: float = 1.0  # s the car must sit still at the start
    countdown: float = 3.5  # s to wait after that, for the start countdown
    timeout: float = 45.0  # s to reach the start before giving up


@dataclass
class BotConfig:
    conditions: Conditions = field(default_factory=lambda: Conditions("stage", "car", "dry"))
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    speed: SpeedConfig = field(default_factory=SpeedConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    learn: LearnConfig = field(default_factory=LearnConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    controls: ControlsConfig = field(default_factory=ControlsConfig)
    reset: ResetConfig = field(default_factory=ResetConfig)


_SECTIONS = {
    "controller": ControllerConfig,
    "speed": SpeedConfig,
    "rules": RulesConfig,
    "learn": LearnConfig,
    "telemetry": TelemetryConfig,
    "controls": ControlsConfig,
    "reset": ResetConfig,
}


def _build(cls: type, values: dict[str, Any], where: str) -> Any:
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(values) - names
    if unknown:
        raise ValueError(f"unknown key(s) in [{where}]: {', '.join(sorted(unknown))}")
    return cls(**values)


def config_from_dict(data: dict[str, Any]) -> BotConfig:
    unknown = set(data) - set(_SECTIONS) - {"conditions"}
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
    cond = data.get("conditions", {})
    missing = {"stage", "car", "weather"} - set(cond)
    if "conditions" in data and missing:
        raise ValueError(f"[conditions] needs {', '.join(sorted(missing))}")
    kwargs: dict[str, Any] = {}
    if cond:
        kwargs["conditions"] = _build(Conditions, cond, "conditions")
    for name, cls in _SECTIONS.items():
        kwargs[name] = _build(cls, data.get(name, {}), name)
    return BotConfig(**kwargs)


def load_config(path: str | Path) -> BotConfig:
    with open(path, "rb") as fh:
        return config_from_dict(tomllib.load(fh))

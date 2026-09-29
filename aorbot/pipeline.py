"""Glue used by the CLI and the tests: the learning loop, driving with the
best profile, and a simulated "human" demonstration for the offline demo."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable

import numpy as np

from .config import BotConfig
from .controller import LineFollower
from .episode import EpisodeResult, StageTracker, VehicleIO, run_episode
from .learner import SpeedProfileLearner
from .line import RacingLine
from .record import states_to_trace
from .sim.car import G, WEATHER_GRIP, SimCarParams
from .sim.io import SimIO
from .sim.stage import SimStage
from .speed import speed_profile
from .types import Action


DEFAULT_GRIP = 7.0


def resolve_grip(cfg: BotConfig, line: RacingLine, log: Callable[[str], None] = print) -> BotConfig:
    """``speed.grip = 0`` means: take the estimate stored with the line."""
    if cfg.speed.grip > 0:
        return cfg
    grip = line.meta.get("grip_estimate")
    if grip is None:
        log(f"no grip estimate stored with the line; using {DEFAULT_GRIP} m/s^2 (set [speed] grip to override)")
        grip = DEFAULT_GRIP
    return dataclasses.replace(cfg, speed=dataclasses.replace(cfg.speed, grip=float(grip)))


def make_learner(line: RacingLine, cfg: BotConfig) -> SpeedProfileLearner:
    if cfg.speed.grip <= 0:
        raise ValueError("resolve the grip first (resolve_grip)")
    return SpeedProfileLearner(line, cfg.speed, cfg.learn, cfg.rules.max_offset, cfg.conditions)


def format_record(rec: dict) -> str:
    text = (
        f"#{rec['iteration']:3d}  {rec['status']:<9}  time {rec['time']:7.2f}s  "
        f"reached {rec['progress']:7.1f} m  mean scale {rec['mean_scale']:.3f}"
    )
    if rec.get("ignored"):
        text += "  (ignored: failed right at the start)"
    if rec.get("new_best"):
        text += "  << new best"
    return text


def learn(
    io: VehicleIO,
    line: RacingLine,
    cfg: BotConfig,
    learner: SpeedProfileLearner,
    iterations: int,
    stop_after_finishes: int | None = None,
    save_path: str | Path | None = None,
    log: Callable[[str], None] = print,
    on_episode: Callable[[EpisodeResult, dict], None] | None = None,
) -> list[EpisodeResult]:
    """Repeat the stage ``iterations`` times, updating the learner after each
    attempt. Optionally stop after ``stop_after_finishes`` finishes in a row."""
    tracker = StageTracker(line, cfg.rules)
    driver = LineFollower(line, cfg.controller, *learner.targets(), grip=cfg.speed.grip)
    results = []
    streak = 0
    for _ in range(iterations):
        driver.set_targets(*learner.targets())
        result = run_episode(io, driver, tracker)
        rec = learner.update(result)
        results.append(result)
        log(format_record(rec))
        if save_path is not None:
            learner.save(save_path)
        if on_episode is not None:
            on_episode(result, rec)
        streak = streak + 1 if result.finished else 0
        if stop_after_finishes and streak >= stop_after_finishes:
            break
    return results


def drive_best(io: VehicleIO, line: RacingLine, cfg: BotConfig, learner: SpeedProfileLearner) -> EpisodeResult:
    driver = LineFollower(line, cfg.controller, *learner.targets(best=True), grip=cfg.speed.grip)
    return run_episode(io, driver, StageTracker(line, cfg.rules))


def sim_config(stage: SimStage, weather: str, base: BotConfig | None = None) -> BotConfig:
    """Config matching the simulator's car and stage."""
    cfg = base or BotConfig()
    p = SimCarParams()
    road_grip = p.mu * WEATHER_GRIP[weather] * G
    return dataclasses.replace(
        cfg,
        speed=dataclasses.replace(cfg.speed, brake_decel=round(0.7 * road_grip, 2)),
        controller=dataclasses.replace(cfg.controller, wheelbase=p.wheelbase, max_steer_angle=p.max_steer),
        # fail just before the trees (which would stop the car dead)
        rules=dataclasses.replace(cfg.rules, max_offset=stage.crash_offset - 0.3),
        conditions=dataclasses.replace(cfg.conditions, stage=f"sim-{stage.seed}", car="sim-car", weather=weather),
    )


def simulate_demo(stage: SimStage, weather: str, cfg: BotConfig, caution: float = 0.55,
                  steer_noise: float = 0.03, seed: int = 0) -> dict[str, np.ndarray]:
    """Stand-in for a human run: a careful drive along the road centre at
    ``caution`` x the car's real grip, with a little steering noise."""
    road_grip = SimCarParams().mu * WEATHER_GRIP[weather] * G
    line = stage.centerline
    speed_cfg = dataclasses.replace(cfg.speed, grip=road_grip * caution, brake_decel=road_grip * caution, v_max=30.0)
    driver = _NoisySteering(LineFollower(line, cfg.controller, speed_profile(line, speed_cfg), grip=road_grip),
                            steer_noise, seed)
    tracker = StageTracker(line, cfg.rules)
    io = SimIO(stage, weather, position_noise=0.05, seed=seed)
    states = []
    result = run_episode(io, driver, tracker, on_step=lambda state, *_: states.append(state))
    if not result.finished:
        raise RuntimeError(f"simulated demonstration did not finish ({result.status.value} at {result.end_s:.0f} m)")
    for _ in range(int(3 / io.dt)):  # stand still past the finish, like a recording would
        states.append(io.step(Action(brake=1.0)))
    return states_to_trace(states)


class _NoisySteering:
    def __init__(self, driver: LineFollower, sigma: float, seed: int):
        self.driver = driver
        self.sigma = sigma
        self.rng = np.random.default_rng(seed)

    def act(self, state, s):
        action, info = self.driver.act(state, s)
        action.steer += float(self.rng.normal(0, self.sigma))
        return action.clipped(), info

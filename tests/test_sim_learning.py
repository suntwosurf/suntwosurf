"""The whole learning loop on short simulated stages (fast, no game needed)."""

import dataclasses

import pytest

from aorbot.pipeline import drive_best, learn, make_learner, resolve_grip, sim_config, simulate_demo
from aorbot.record import build_line
from aorbot.sim.io import SimIO
from aorbot.sim.stage import make_stage


def setup(seed, weather="dry", length=800.0, init_scale=0.7):
    stage = make_stage(seed, length=length)
    cfg = sim_config(stage, weather)
    line, grip = build_line(simulate_demo(stage, weather, cfg, seed=seed))
    cfg = resolve_grip(cfg, line, log=lambda m: None)
    cfg = dataclasses.replace(cfg, learn=dataclasses.replace(cfg.learn, init_scale=init_scale))
    return stage, line, cfg


def test_stage_is_long_enough_and_line_follows_it():
    stage, line, _ = setup(5)
    assert stage.centerline.length > 750
    assert abs(line.length - stage.centerline.length) < 20


def test_cautious_start_finishes_first_try_then_gets_faster():
    stage, line, cfg = setup(5)
    learner = make_learner(line, cfg)
    results = learn(SimIO(stage), line, cfg, learner, 8, log=lambda m: None)
    assert results[0].finished
    assert learner.best["time"] < results[0].time * 0.95


@pytest.mark.parametrize("weather", ["dry", "snow"])
def test_too_fast_start_learns_to_finish(weather):
    stage, line, cfg = setup(4, weather, init_scale=1.4)
    learner = make_learner(line, cfg)
    results = learn(SimIO(stage, weather), line, cfg, learner, 30, stop_after_finishes=3, log=lambda m: None)
    assert not results[0].finished, "should start too fast and crash"
    assert all(r.finished for r in results[-3:])
    assert drive_best(SimIO(stage, weather), line, cfg, learner).finished

import numpy as np
import pytest

from aorbot.config import LearnConfig, SpeedConfig
from aorbot.episode import EpisodeResult, Status
from aorbot.learner import SpeedProfileLearner
from aorbot.line import RacingLine
from aorbot.types import Conditions

COND = Conditions("finland 1", "group 2", "dry")


def make(**learn):
    a = np.linspace(0, 2.0, 201)  # 400 m arc of radius 200 m: corner speed ~37 m/s < v_max
    line = RacingLine(np.column_stack([200 * np.sin(a), 200 * (1 - np.cos(a))]))
    cfg = LearnConfig(bin_length=20.0, init_scale=1.0, **learn)
    return SpeedProfileLearner(line, SpeedConfig(grip=7.0), cfg, max_offset=6.0, conditions=COND)


def result(status, end_s, offsets=None, time=50.0):
    s = np.arange(0.0, end_s, 1.0)
    off = np.zeros_like(s) if offsets is None else offsets(s)
    return EpisodeResult(status, time, float(end_s), float(end_s), {"s": s, "offset": off})


def test_failure_slows_the_lead_in_and_sets_a_limit():
    L = make(fail_lookback=60.0)
    L.update(result(Status.OFF_STAGE, 205.0))
    bins = np.arange(L.n_bins)
    failed = (bins >= 7) & (bins <= 10)  # 145 m .. 205 m
    assert np.allclose(L.scale[failed], 0.85)
    assert np.allclose(L.limit[failed], 1.0)
    assert np.allclose(L.scale[bins < 7], 1.05)  # clean part before it got faster
    assert np.allclose(L.scale[bins > 10], 1.0)  # never reached: unchanged
    assert L.best is None


def test_clean_bins_grow_but_stay_under_their_limit():
    L = make()
    L.update(result(Status.OFF_STAGE, 205.0))
    for _ in range(10):
        L.update(result(Status.FINISHED, 400.0))
    assert L.scale[9] == pytest.approx(0.95)  # limit 1.0 x limit_margin
    assert L.scale[0] == pytest.approx(1.6)  # no limit known: grows to max_scale


def test_near_miss_slows_bin_and_the_ones_before():
    L = make(warn_back=2)
    L.update(result(Status.FINISHED, 400.0, lambda s: np.where((s > 100) & (s < 120), 4.5, 0.0)))
    assert np.allclose(L.scale[3:6], 0.97)
    assert L.scale[6] == pytest.approx(1.05)


def test_best_keeps_the_parameters_that_were_driven():
    L = make()
    L.update(result(Status.FINISHED, 400.0, time=60.0))
    assert L.best["time"] == 60.0 and np.allclose(L.best["scale"], 1.0)
    L.update(result(Status.FINISHED, 400.0, time=58.0))
    assert L.best["time"] == 58.0 and np.allclose(L.best["scale"], 1.05)
    L.update(result(Status.FINISHED, 400.0, time=59.0))
    assert L.best["time"] == 58.0
    v_best, _ = L.targets(best=True)
    v_now, _ = L.targets()
    assert np.all(v_now > v_best)


def test_stuck_at_the_start_is_ignored():
    L = make()
    rec = L.update(result(Status.STUCK, 3.0))
    assert rec["ignored"] and np.allclose(L.scale, 1.0)


def test_save_load_roundtrip_and_conditions_guard(tmp_path):
    L = make()
    L.update(result(Status.OFF_STAGE, 205.0))
    L.update(result(Status.FINISHED, 400.0))
    L.save(tmp_path / "state.json")
    back = make()
    back.load(tmp_path / "state.json")
    assert np.allclose(back.scale, L.scale) and np.array_equal(back.limit, L.limit)
    assert back.best == L.best and back.iteration == 2
    other = SpeedProfileLearner(L.line, L.speed_cfg, L.cfg, 6.0, Conditions("finland 1", "group 2", "snow"))
    with pytest.raises(ValueError, match="exact stage"):
        other.load(tmp_path / "state.json")


def test_stop_in_finish_zone_counts_as_finished():
    from aorbot.config import RulesConfig
    from aorbot.episode import StageTracker
    from aorbot.types import CarState

    line = RacingLine(np.column_stack([np.linspace(0, 400, 201), np.zeros(201)]))

    def drive(finish_zone):
        tracker = StageTracker(line, RulesConfig(finish_zone=finish_zone, stuck_time=2.0, start_grace=0.0))
        tracker.reset(CarState(0.0, 0.0, 0.0, 0.0, 0.0))
        t, x = 0.0, 0.0
        while True:
            t += 0.1
            x = min(x + 2.0, 360.0)  # the game stops the car 40 m before the recorded end
            s, off, status = tracker.update(CarState(t, x, 0.0, 0.0, 20.0 if x < 360 else 0.0))
            if status is not Status.RUNNING:
                return status, tracker

    status, _ = drive(0.0)
    assert status is Status.STUCK
    status, tracker = drive(60.0)
    assert status is Status.FINISHED
    assert tracker.finish_t - tracker.t0 == pytest.approx(17.1, abs=0.15)  # entered the zone at 340 m

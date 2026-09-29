import numpy as np
import pytest

from aorbot.config import SpeedConfig
from aorbot.line import RacingLine
from aorbot.speed import braking_pass, speed_profile


def test_braking_pass_respects_deceleration():
    v = np.array([40.0, 40.0, 40.0, 40.0, 10.0, 40.0])
    ds = np.full(5, 10.0)
    out = braking_pass(v, ds, decel=5.0)
    assert out[4] == 10.0
    assert np.all(out[:-1] ** 2 <= out[1:] ** 2 + 2 * 5.0 * ds + 1e-9)
    assert out[3] == pytest.approx(np.sqrt(100 + 100))


def test_profile_slows_for_corner_and_scale_applies():
    straight = np.column_stack([np.linspace(0, 300, 151), np.zeros(151)])
    a = np.linspace(0, np.pi / 2, 50)
    corner = np.column_stack([300 + 20 * np.sin(a), 20 * (1 - np.cos(a))])
    line = RacingLine(np.vstack([straight, corner[1:]]))
    cfg = SpeedConfig(grip=8.0, brake_decel=6.0, v_max=50.0, v_min=4.0)
    v = speed_profile(line, cfg)
    in_corner = line.s > 320
    assert v[in_corner].min() == pytest.approx(np.sqrt(8.0 * 20), rel=0.1)
    assert v[0] == pytest.approx(50.0)
    slower = speed_profile(line, cfg, scale=np.full(len(line.s), 0.5))
    assert slower[in_corner].min() == pytest.approx(0.5 * v[in_corner].min(), rel=0.02)

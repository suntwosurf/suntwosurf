import math

import numpy as np
import pytest

from aorbot.line import RacingLine


def arc(radius=50.0, sweep=math.pi, n=400, left=True):
    a = np.linspace(0, sweep, n)
    sign = 1 if left else -1
    # starts at the origin heading +x, turning left (CCW) or right
    return np.column_stack([radius * np.sin(a), sign * radius * (1 - np.cos(a))])


def test_length_and_curvature_of_arc():
    line = RacingLine(arc(radius=50.0))
    assert line.length == pytest.approx(50 * math.pi, rel=1e-3)
    mid = slice(len(line.s) // 4, 3 * len(line.s) // 4)
    assert np.allclose(line.kappa[mid], 1 / 50.0, rtol=0.02)
    right = RacingLine(arc(radius=50.0, left=False))
    assert np.allclose(right.kappa[mid], -1 / 50.0, rtol=0.02)


def test_projection_offset_sign_and_progress():
    line = RacingLine([[0, 0], [100, 0], [200, 0]])
    p = line.project(30.0, 2.0)
    assert p.s == pytest.approx(30.0)
    assert p.offset == pytest.approx(2.0)  # left of travel direction is positive
    assert line.project(150.0, -3.0).offset == pytest.approx(-3.0)


def test_projection_hint_avoids_jumping_to_nearby_part_of_stage():
    # hairpin: the way back runs 10 m beside the way out
    out_leg = np.column_stack([np.linspace(0, 200, 101), np.zeros(101)])
    back = np.column_stack([np.linspace(200, 0, 101), np.full(101, 10.0)])
    line = RacingLine(np.vstack([out_leg, back[1:]]))
    x, y = 50.0, 6.0  # closer to the way back (4 m) than to the way out (6 m)
    assert line.project(x, y).s > 200  # global search picks the way back
    assert line.project(x, y, s_hint=48.0, window=40.0).s == pytest.approx(50.0, abs=0.5)


def test_from_trace_smooths_noise_and_roundtrips(tmp_path):
    rng = np.random.default_rng(0)
    xy = arc(radius=80.0, sweep=math.pi / 2, n=2000) + rng.normal(0, 0.15, (2000, 2))
    xy = np.vstack([np.repeat(xy[:1], 50, axis=0), xy])  # standing at the start
    line = RacingLine.from_trace(xy)
    mid = slice(len(line.s) // 5, 4 * len(line.s) // 5)
    assert np.allclose(line.kappa[mid], 1 / 80.0, atol=0.004)
    line.meta = {"grip_estimate": 6.5}
    line.save(tmp_path / "line.npz")
    back = RacingLine.load(tmp_path / "line.npz")
    assert np.allclose(back.xy, line.xy)
    assert back.meta == {"grip_estimate": 6.5}

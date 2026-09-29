"""import-path: pick the stage path out of the plugin's dump."""

import json
import math

import numpy as np
import pytest

from aorbot.cli import main
from aorbot.import_path import load_candidates, pick
from aorbot.line import RacingLine


def centreline():
    """1.2 km: straight, 90 deg left of radius 60 m, straight."""
    a = np.linspace(0, math.pi / 2, 60)
    pts = np.vstack([
        np.column_stack([np.linspace(0, 500, 101), np.zeros(101)]),
        np.column_stack([500 + 60 * np.sin(a), 60 * (1 - np.cos(a))]),
        np.column_stack([np.full(100, 560.0), np.linspace(61, 600, 100)]),
    ])
    return pts


def to_unity(xy, y=5.0):
    return [[float(x), y, float(z)] for x, z in xy]


@pytest.fixture
def dump(tmp_path):
    road = centreline()
    rng = np.random.default_rng(1)
    sparse_reversed = road[::-1][::8]  # every ~40 m, stored finish-to-start
    fence = road + np.array([0.0, 6.0])  # parallel line 6 m to the side
    data = {
        "format": 1,
        "scene": "stage_test",
        "car": [30.0, 5.0, 0.5],  # 30 m into the path, on the road
        "car_forward": [1.0, 0.0, 0.0],
        "candidates": [
            {"source": "Fence.posts", "points": to_unity(fence)},
            {"source": "StagePath.nodes", "points": to_unity(sparse_reversed)},
            {"source": "Trees.positions", "points": to_unity(rng.uniform(-200, 600, (300, 2)))},
            {"source": "Sign.markers", "points": to_unity(road[:40])},
        ],
        "types": {"StagePath": 1},
    }
    path = tmp_path / "paths_latest.json"
    path.write_text(json.dumps(data))
    return path


def test_picks_the_road_centre_and_orients_it(dump):
    _, cands = load_candidates(dump)
    assert [c.ok for c in cands] == [True, True, False, False]
    assert "kinks" in " ".join(cands[2].problems) or "jumps" in " ".join(cands[2].problems)
    assert "only" in " ".join(cands[3].problems)
    chosen = pick(cands)
    assert chosen.source == "StagePath.nodes"  # closer to the car than the fence
    assert np.allclose(chosen.points[0], [30.0, 0.0], atol=1.0)  # starts at the car


def test_import_path_command_builds_a_smooth_line(dump, tmp_path, capsys):
    out = tmp_path / "line.npz"
    assert main(["import-path", "--dump", str(dump), "--out", str(out)]) == 0
    assert "using [1]" in capsys.readouterr().out
    line = RacingLine.load(out)
    assert line.length == pytest.approx(1164 - 30, rel=0.03)
    corner = (line.s > 500) & (line.s < 540)
    assert np.allclose(line.kappa[corner], 1 / 60, rtol=0.25)  # sparse nodes rebuilt as a curve
    assert math.cos(line.heading[0]) > 0.99  # points the way the car faces
    assert line.meta["source"].startswith("game path: StagePath.nodes")


def test_no_usable_path(tmp_path, capsys):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"scene": "menu", "car": [0, 0, 0], "car_forward": [1, 0, 0],
                                "candidates": [], "types": {}}))
    assert main(["import-path", "--dump", str(path), "--out", str(tmp_path / "l.npz")]) == 1
    assert "no usable stage path" in capsys.readouterr().out

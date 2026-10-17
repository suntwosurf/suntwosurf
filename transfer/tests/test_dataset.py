import json

import numpy as np
import pytest

from rtsconcepts.dataset import RecordingError, load_dir, load_game, split_by_game, stack
from rtsconcepts.spec import ACTIONS, FEATURES, PRIVILEGED, SPEC_VERSION


def header(p, **kw):
    return {"p": p, "header": True, "v": SPEC_VERSION, "step": 10, "features": FEATURES, "privileged": PRIVILEGED,
            "actions": ACTIONS, "civ": "athen", "difficulty": 3, "behavior": "balanced", **kw}


def step(p, t, a=None):
    return {"p": p, "t": t, "f": [t / 60] + [float(p)] * (len(FEATURES) - 1), "x": [1.0] * len(PRIVILEGED), "a": a or {}}


def write_game(path, winners=(1,), players=(1, 2), h=header):
    lines = [{"game": {"winners": list(winners), "reason": "conquest"}}]
    for p in players:
        lines.append(h(p))
    for t, a in [(10, {"TRAIN_WORKER": 5}), (20, {"BUILD_SUPPLY": 1}), (30, {"ATTACK": 1, "TRAIN_ARMY": 3}), (40, {})]:
        for p in players:
            lines.append(step(p, t, a))
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_states_are_paired_with_the_decisions_that_follow(tmp_path):
    write_game(tmp_path / "1-0.jsonl")
    trajs = load_game(tmp_path / "1-0.jsonl")
    assert [(t.player, t.won) for t in trajs] == [(1, True), (2, False)]
    t = trajs[0]
    assert list(t.times) == [10, 20, 30]  # the last state has no following decisions
    assert t.features.shape == (3, len(FEATURES)) and t.privileged.shape == (3, len(PRIVILEGED))
    col = ACTIONS.index
    # state at 10 s -> decided until 20 s: a house; at 20 s -> attack with 3 soldiers; at 30 s -> nothing
    assert t.actions[0, col("BUILD_SUPPLY")] == 1 and t.actions[0].sum() == 1
    assert t.actions[1, col("ATTACK")] == 1 and t.actions[1, col("TRAIN_ARMY")] == 3
    assert t.actions[2].sum() == 0


def test_other_feature_list_is_refused(tmp_path):
    write_game(tmp_path / "old.jsonl", h=lambda p: header(p, features=FEATURES[:-1]))
    with pytest.raises(RecordingError, match="record again"):
        load_game(tmp_path / "old.jsonl")


def test_split_keeps_games_together(tmp_path):
    for i in range(10):
        write_game(tmp_path / f"1-{i}.jsonl", winners=())
    trajs = load_dir(tmp_path)
    assert len(trajs) == 20 and all(t.won is None for t in trajs)
    train, held = split_by_game(trajs, holdout=0.2, seed=1)
    assert len(held) == 4 and {t.game for t in train}.isdisjoint({t.game for t in held})
    x, p, a = stack(held)
    assert x.shape == (12, len(FEATURES)) and a.shape == (12, len(ACTIONS)) and p.dtype == np.float32

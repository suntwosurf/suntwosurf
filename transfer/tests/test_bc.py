import json

import numpy as np

from rtsconcepts import bc
from rtsconcepts.cli import main
from rtsconcepts.dataset import load_dir, split_by_game
from rtsconcepts.inspect import inspect
from rtsconcepts.spec import ACTIONS, FEATURES, PRIVILEGED, SPEC_VERSION

F = FEATURES.index


def make_recordings(folder, games=30, steps=60, seed=0):
    """A teacher whose decisions follow simple rules on the features:
    workers while there is room, a house when supply runs out, an attack with
    a big army; EXPAND is pure chance (nothing to learn)."""
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    for g in range(games):
        lines = [{"game": {"winners": [1], "reason": "conquest"}}]
        for p in (1, 2):
            lines.append({"p": p, "header": True, "v": SPEC_VERSION, "step": 10, "features": FEATURES,
                          "privileged": PRIVILEGED, "actions": ACTIONS, "civ": "athen", "difficulty": 3,
                          "behavior": "balanced"})
            prev_decision = {}
            for k in range(steps):
                f = rng.uniform(0, 1, len(FEATURES))
                f[F("t_min")] = k / 6
                f[F("army_value")] = rng.uniform(0, 20)
                lines.append({"p": p, "t": 10 * (k + 1), "f": f.round(3).tolist(), "x": [0.0] * len(PRIVILEGED),
                              "a": prev_decision})
                # what the teacher decides after seeing this state (logged with the next step)
                prev_decision = {}
                if f[F("supply_headroom")] > 0.3:
                    prev_decision["TRAIN_WORKER"] = 3
                else:
                    prev_decision["BUILD_SUPPLY"] = 1
                if f[F("army_value")] > 12:
                    prev_decision["ATTACK"] = 1
                if rng.random() < 0.3:
                    prev_decision["EXPAND"] = 1
        (folder / f"1-{g}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_bc_learns_the_rules_and_not_the_noise(tmp_path):
    make_recordings(tmp_path / "rec")
    train, held = split_by_game(load_dir(tmp_path / "rec"), holdout=0.2, seed=0)
    model = bc.train(train, hidden=(32,), epochs=40, seed=0)
    s = bc.score(model, held)
    per = s["per_action"]
    for name in ["TRAIN_WORKER", "BUILD_SUPPLY", "ATTACK"]:
        assert per[name]["balanced"] > 0.93, (name, per[name])
    assert abs(per["EXPAND"]["balanced"] - 0.5) < 0.1  # chance: nothing to learn
    assert per["EXPAND"]["model"] <= per["EXPAND"]["majority"] + 0.02
    assert per["TECH_UP"]["rate"] == 0 and per["TECH_UP"]["model"] == 1.0
    text = "\n".join(bc.report(s))
    assert "always-majority" in text and "balanced" in text

    # saved and loaded, it predicts the same
    model.save(tmp_path / "bc.json")
    back = bc.BCModel.from_dict(json.loads((tmp_path / "bc.json").read_text()))
    x = held[0].features
    assert np.allclose(back.predict(x), model.predict(x))


def test_cli(tmp_path, capsys):
    make_recordings(tmp_path / "rec", games=10, steps=30)
    assert main(["inspect", str(tmp_path / "rec")]) == 0
    out = capsys.readouterr().out
    assert "10 games, 20 player recordings" in out and "TRAIN_WORKER" in out
    assert main(["bc", str(tmp_path / "rec"), "--epochs", "5", "--out", str(tmp_path / "bc.json")]) == 0
    assert "games it never saw" in capsys.readouterr().out and (tmp_path / "bc.json").is_file()
    assert main(["bc", str(tmp_path / "missing")]) == 1


def test_inspect_flags_bad_values(tmp_path):
    make_recordings(tmp_path / "rec", games=2, steps=10)
    trajs = load_dir(tmp_path / "rec")
    trajs[0].features[0, F("stock")] = -1
    trajs[0].features[1, F("explored")] = 3
    for t in trajs:
        t.features[:, F("bases")] = 1
    lines = inspect(trajs)
    row = {line.split()[0]: line for line in lines if line.split() and line.split()[0] in FEATURES}
    assert "1 negative" in row["stock"] and "1 above 1" in row["explored"] and "never changes" in row["bases"]
    assert row["income"].endswith("ok")

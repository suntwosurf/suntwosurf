import json

import numpy as np

from rtsconcepts import bc
from rtsconcepts.cli import main
from rtsconcepts.dataset import load_dir, split_by_game
from rtsconcepts.inspect import inspect
from rtsconcepts.spec import ACTIONS, FEATURES, PRIVILEGED, SPEC_VERSION

F = FEATURES.index


def make_recordings(folder, games=30, steps=60, seed=0, rhythm=0):
    """A teacher whose decisions follow simple rules on the features:
    workers while there is room, a house when supply runs out, an attack with
    a big army; EXPAND is pure chance (nothing to learn). With ``rhythm``,
    TECH_UP comes every ``rhythm`` steps: only a memory of its own decisions
    can tell when. The true enemy army (privileged) is about 10."""
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    for g in range(games):
        lines = [{"game": {"winners": [1], "reason": "conquest"}}]
        for p in (1, 2):
            lines.append({"p": p, "header": True, "v": SPEC_VERSION, "step": 10, "features": FEATURES,
                          "privileged": PRIVILEGED, "actions": ACTIONS, "civ": "athen", "difficulty": 3,
                          "behavior": "balanced"})
            prev_decision = {}
            offset = int(rng.integers(rhythm)) if rhythm else 0
            for k in range(steps):
                f = rng.uniform(0, 1, len(FEATURES))
                f[F("t_min")] = k / 6
                f[F("army_value")] = rng.uniform(0, 20)
                x = [10 * rng.uniform(0.9, 1.1), 5.0, 1.0, 1.0]
                lines.append({"p": p, "t": 10 * (k + 1), "f": f.round(3).tolist(), "x": np.round(x, 3).tolist(),
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
                if rhythm and (k + offset) % rhythm == 0:
                    prev_decision["TECH_UP"] = 1
        (folder / f"1-{g}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_bc_learns_the_rules_and_not_the_noise(tmp_path):
    make_recordings(tmp_path / "rec")
    train, held = split_by_game(load_dir(tmp_path / "rec"), holdout=0.2, seed=0)
    model = bc.train(train, hidden=(32,), epochs=40, seed=0)
    s = bc.score(model, held)
    per = s["per_action"]
    for name in ["TRAIN_WORKER", "BUILD_SUPPLY", "ATTACK"]:
        assert per[name]["balanced"] > 0.93 and per[name]["auc"] > 0.97 and per[name]["skill"] > 0.6, (name, per[name])
    assert abs(per["EXPAND"]["balanced"] - 0.5) < 0.1 and abs(per["EXPAND"]["auc"] - 0.5) < 0.1  # chance
    assert per["EXPAND"]["skill"] < 0.03  # no better than the base rate
    assert per["EXPAND"]["model"] <= per["EXPAND"]["majority"] + 0.02
    # never happens: no AUC or skill, and a 50 % cut-off is always right
    assert per["TECH_UP"]["rate"] == 0 and per["TECH_UP"]["model"] == 1.0 and per["TECH_UP"]["auc"] != per["TECH_UP"]["auc"]
    text = "\n".join(bc.report(s))
    assert "AUC" in text and "skill" in text and "always-majority" in text and "balanced" in text

    # saved and loaded, it predicts the same
    model.save(tmp_path / "bc.json")
    back = bc.BCModel.from_dict(json.loads((tmp_path / "bc.json").read_text()))
    assert np.allclose(back.predict_trajs(held), model.predict_trajs(held))
    assert np.allclose(back.rate, model.rate) and back.memory


def test_memory_learns_the_teachers_rhythm(tmp_path):
    """TECH_UP every 5 steps depends on nothing in the state: only the memory
    of its own decisions tells when the next one is due."""
    make_recordings(tmp_path / "rec", games=30, steps=60, rhythm=5)
    train, held = split_by_game(load_dir(tmp_path / "rec"), holdout=0.2, seed=0)
    with_memory = bc.score(bc.train(train, hidden=(32,), epochs=60, seed=0), held)["per_action"]["TECH_UP"]
    without = bc.score(bc.train(train, hidden=(32,), epochs=60, seed=0, memory=False), held)["per_action"]["TECH_UP"]
    assert with_memory["auc"] > 0.95 and abs(without["auc"] - 0.5) < 0.1, (with_memory, without)


def test_old_models_without_memory_still_load(tmp_path):
    make_recordings(tmp_path / "rec", games=6, steps=20)
    trajs = load_dir(tmp_path / "rec")
    model = bc.train(trajs, hidden=(8,), epochs=2, memory=False)
    d = model.to_dict()
    del d["memory"], d["rate"]  # as saved before memory existed
    back = bc.BCModel.from_dict(d)
    assert not back.memory and np.allclose(back.predict(trajs[0].features), model.predict(trajs[0].features))


def test_auc():
    y = np.array([0, 0, 1, 1], bool)
    assert bc.auc(np.array([0.1, 0.2, 0.3, 0.4]), y) == 1.0
    assert bc.auc(np.array([0.4, 0.3, 0.2, 0.1]), y) == 0.0
    assert bc.auc(np.full(4, 0.5), y) == 0.5  # ties count half
    assert bc.auc(np.array([0.1, 0.3, 0.2, 0.4]), y) == 0.75
    assert bc.auc(np.array([0.1, 0.2]), np.array([0, 0], bool)) != bc.auc(np.array([0.1, 0.2]), np.array([0, 0], bool))


def test_cli(tmp_path, capsys):
    make_recordings(tmp_path / "rec", games=10, steps=30)
    assert main(["inspect", str(tmp_path / "rec")]) == 0
    out = capsys.readouterr().out
    assert "10 games, 20 player recordings" in out and "TRAIN_WORKER" in out and "MILITARY_ADVANTAGE" in out
    assert main(["bc", str(tmp_path / "rec"), "--epochs", "5", "--out", str(tmp_path / "bc.json")]) == 0
    assert "games it never saw" in capsys.readouterr().out and (tmp_path / "bc.json").is_file()
    assert main(["bc", str(tmp_path / "missing")]) == 1
    assert main(["student", str(tmp_path / "rec"), "--epochs", "3", "--compare", "--out", str(tmp_path / "s.json")]) == 0
    out = capsys.readouterr().out
    assert "explained" in out and "without the concept bottleneck" in out and (tmp_path / "s.json").is_file()
    assert main(["curve", str(tmp_path / "rec"), "--epochs", "3", "--points", "2"]) == 0
    assert "the last doubling" in capsys.readouterr().out


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

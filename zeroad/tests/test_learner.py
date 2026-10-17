import dataclasses
import json

import pytest

from zadbot.config import LearnConfig, MatchConfig
from zadbot.learner import CrossEntropyLearner, load_centre, make_scenarios
from zadbot.params import PARAMS, defaults, to_unit, BY_NAME


def test_scenarios_deterministic_and_fair():
    m = MatchConfig(civs=["athen", "rome", "gaul"], maps=["random/mainland", "random/continent"])
    a = make_scenarios(m, 6, seed=3)
    assert a == make_scenarios(m, 6, seed=3)
    assert [s.bot_side for s in a] == [1, 2, 1, 2, 1, 2]
    assert [s.map for s in a[:2]] == ["random/mainland", "random/continent"]
    assert all(s.bot_civ == s.opponent_civ for s in a)  # mirror
    spec = a[1].spec("zadbot_t0", m)
    assert [p.ai for p in spec.players] == ["petra", "zadbot_t0"]
    assert spec.time_limit == m.time_limit


def test_first_candidate_is_the_centre():
    lr = CrossEntropyLearner(LearnConfig(population=5, elite=2), MatchConfig())
    cands = lr.candidates()
    assert len(cands) == 5
    assert cands[0] == lr.mean
    assert all(0 <= u <= 1 for c in cands for u in c.values())
    assert cands == lr.candidates()  # reproducible (resume replays the same generation)


def test_cem_finds_optimum_of_synthetic_fitness():
    """No game: fitness = closeness to a hidden target in unit space."""
    target = {p.name: 0.2 + 0.6 * (i % 2) for i, p in enumerate(PARAMS)}
    lr = CrossEntropyLearner(LearnConfig(population=12, elite=4, smoothing=0.7, init_std=0.3, seed=5), MatchConfig())

    def fitness(u):
        return -sum((u[k] - target[k]) ** 2 for k in u)

    start = fitness(lr.mean)
    for _ in range(40):
        cands = lr.candidates()
        lr.update(cands, [fitness(c) for c in cands])
    assert fitness(lr.mean) > 0.2 * start  # much closer than plain Petra
    assert lr.curve()[-1][1] > lr.curve()[0][1]
    assert all(s >= lr.cfg.min_std for s in lr.std.values())


def test_save_load_round_trip(tmp_path):
    cfg, m = LearnConfig(population=4, elite=2), MatchConfig()
    lr = CrossEntropyLearner(cfg, m)
    cands = lr.candidates()
    lr.update(cands, [0.1, 0.5, -0.2, 0.0], [{"outcomes": [0.1]}] * 4)
    path = tmp_path / "learner.json"
    lr.save(path)
    back = CrossEntropyLearner.load(path, cfg, m)
    assert back.generation == 1
    assert back.mean == pytest.approx(lr.mean)
    assert back.history[0]["candidates"][1]["fitness"] == 0.5
    assert load_centre(path) == pytest.approx(lr.centre_values())

    with pytest.raises(ValueError, match="different \\[match\\]"):
        CrossEntropyLearner.load(path, cfg, dataclasses.replace(m, difficulty=5))
    data = json.loads(path.read_text())
    data["game_version"] = "0.27.1"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="0.27.1"):
        CrossEntropyLearner.load(path, cfg, m)


def test_centre_starts_at_plain_petra():
    lr = CrossEntropyLearner(LearnConfig(), MatchConfig())
    assert lr.centre_values() == pytest.approx(defaults())
    assert lr.mean["workers"] == pytest.approx(to_unit(BY_NAME["workers"], 1.0))

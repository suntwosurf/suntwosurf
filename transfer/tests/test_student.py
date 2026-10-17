import json
from types import SimpleNamespace

import numpy as np
import pytest

from rtsconcepts import bc, concepts, memory, student
from rtsconcepts.dataset import load_dir, split_by_game
from rtsconcepts.policies import BCPolicy
from rtsconcepts.spec import ACTIONS, CONCEPTS, FEATURES, MEMORY, MEMORY_CAP, MEMORY_STEPS, PRIVILEGED

from test_bc import make_recordings

A = ACTIONS.index
C = CONCEPTS.index
F = FEATURES.index


def test_memory_counts_recent_decisions_and_steps_since():
    m = memory.Memory()
    v = m.vector()
    assert v.shape == (len(MEMORY),)
    assert (v[:len(ACTIONS)] == 0).all() and (v[len(ACTIONS):] == 1).all()  # nothing yet, "never"
    m.observe({"ATTACK": 1, "TRAIN_WORKER": 3})
    m.observe({})
    v = m.vector()
    recent, since = v[:len(ACTIONS)], v[len(ACTIONS):]
    assert recent[A("ATTACK")] == 1 / MEMORY_STEPS and since[A("ATTACK")] == 2 / MEMORY_CAP
    assert recent[A("EXPAND")] == 0 and since[A("EXPAND")] == 1
    for _ in range(MEMORY_STEPS):
        m.observe({})
    assert m.vector()[A("ATTACK")] == 0  # out of the last minute, still remembered as "since"
    assert m.vector()[len(ACTIONS) + A("ATTACK")] == (2 + MEMORY_STEPS) / MEMORY_CAP


def test_memory_of_a_recording_is_what_play_would_see():
    actions = np.zeros((8, len(ACTIONS)))
    actions[0, A("TECH_UP")] = 1
    actions[3, A("TRAIN_ARMY")] = 4
    traj = SimpleNamespace(actions=actions)
    mem = memory.of(traj)
    m = memory.Memory()
    for k in range(len(actions)):
        assert np.allclose(mem[k], m.vector())
        m.observe({a: actions[k, j] for j, a in enumerate(ACTIONS)})
    assert mem[0, len(ACTIONS) + A("TECH_UP")] == 1 and mem[1, len(ACTIONS) + A("TECH_UP")] == 1 / MEMORY_CAP


def traj_with(rows, privileged=None, actions=None):
    """A trajectory from {feature: value} rows."""
    f = np.zeros((len(rows), len(FEATURES)))
    for k, row in enumerate(rows):
        for name, value in row.items():
            f[k, F(name)] = value
    z = np.zeros((len(rows), len(PRIVILEGED))) if privileged is None else np.array(privileged, float)
    a = np.zeros((len(rows), len(ACTIONS))) if actions is None else actions
    return SimpleNamespace(features=f, privileged=z, actions=a)


def test_concept_labels_worked_out_by_hand():
    # 1 production building + 1 base: capacity 10 per minute
    rows = [
        {"production_buildings": 1, "bases": 1, "stock": 5, "income": 5, "supply_headroom": 0.05,
         "army_value": 30, "threat_home": 10, "lost_value": 9},
        {"production_buildings": 1, "bases": 1, "stock": 30, "income": 25, "supply_headroom": 0.5},
        {"production_buildings": 1, "bases": 1, "stock": 20, "income": 0, "supply_headroom": 0.5},
    ]
    z = [[10, 0, 0, 15], [0, 0, 0, 0], [0, 0, 0, 0]]
    actions = np.zeros((3, len(ACTIONS)))
    actions[0, A("TRAIN_WORKER")] = 4          # 4 * 0.5 = 2 on the economy
    actions[0, A("BUILD_PRODUCTION")] = 1      # 3 on the military
    actions[1, A("EXPAND")] = 1                # 6 more on the economy
    c = concepts.labels(traj_with(rows, z, actions))
    assert c.shape == (3, len(CONCEPTS)) and (c >= 0).all() and (c <= 1).all()
    assert c[0, C("RESOURCE_SCARCITY")] == pytest.approx(1 - 10 / 20)
    assert c[1, C("RESOURCE_SCARCITY")] == 0
    assert c[0, C("PRODUCTION_BOTTLENECK")] == 0 and c[1, C("PRODUCTION_BOTTLENECK")] == 1
    assert c[2, C("PRODUCTION_BOTTLENECK")] == pytest.approx(0.5)  # 2 minutes of capacity in stock
    assert c[0, C("SUPPLY_BLOCK")] == pytest.approx(0.5) and c[1, C("SUPPLY_BLOCK")] == 0
    # decisions before each state: none before the first
    assert c[0, C("ECONOMIC_INVESTMENT")] == 0.5
    assert c[1, C("ECONOMIC_INVESTMENT")] == pytest.approx(2 / 5)
    assert c[2, C("ECONOMIC_INVESTMENT")] == pytest.approx(8 / 11)
    assert c[0, C("MILITARY_ADVANTAGE")] == pytest.approx(30 / 40) and c[1, C("MILITARY_ADVANTAGE")] == 0.5
    assert c[0, C("ECONOMIC_ADVANTAGE")] == pytest.approx(5 / 20) and c[1, C("ECONOMIC_ADVANTAGE")] == 1
    assert c[0, C("DEFENSIVE_PRESSURE")] == pytest.approx(19 / 50) and c[1, C("DEFENSIVE_PRESSURE")] == 0


def test_student_learns_concepts_and_decisions(tmp_path):
    make_recordings(tmp_path / "rec", games=30, steps=60, rhythm=5)
    train, held = split_by_game(load_dir(tmp_path / "rec"), holdout=0.2, seed=0)
    model = student.train(train, epochs=60, seed=0)
    s = bc.score(model, held)["per_action"]
    for name in ["TRAIN_WORKER", "BUILD_SUPPLY", "ATTACK", "TECH_UP"]:
        assert s[name]["auc"] > 0.95, (name, s[name])
    assert abs(s["EXPAND"]["auc"] - 0.5) < 0.1
    c = student.score_concepts(model, held)
    # the true enemy army and income are hidden; the test teacher makes them predictable
    assert c["MILITARY_ADVANTAGE"]["explained"] > 0.6 and c["ECONOMIC_ADVANTAGE"]["explained"] > 0.6, c
    assert "explained" in "\n".join(student.report_concepts(c))

    # the policy really only sees the concepts, the memory and its few raw features
    assert model.policy.W[0].shape[0] == len(CONCEPTS) + len(student.POLICY_FEATURES) + len(MEMORY)

    model.save(tmp_path / "s.json")
    back = student.ConceptStudent.from_dict(json.loads((tmp_path / "s.json").read_text()))
    assert np.allclose(back.predict_trajs(held), model.predict_trajs(held))
    assert np.allclose(back.concepts_trajs(held), model.concepts_trajs(held))
    with pytest.raises(ValueError):  # made for other concepts
        student.ConceptStudent.from_dict(model.to_dict() | {"concepts": CONCEPTS[:-1]})


def test_policy_keeps_its_own_memory(tmp_path):
    make_recordings(tmp_path / "rec", games=20, steps=60, rhythm=5)
    model = bc.train(load_dir(tmp_path / "rec"), hidden=(32,), epochs=60, seed=0)
    policy = BCPolicy(model, sample=False)
    f = [0.5] * len(FEATURES)
    f[F("supply_headroom")], f[F("army_value")] = 0.9, 1
    techs = []
    for k in range(20):
        f[F("t_min")] = k / 6
        if "TECH_UP" in policy(f):
            techs.append(k)
    # in rhythm, not every step: it remembers the TECH_UP it just made
    assert len(techs) >= 3 and min(np.diff(techs)) >= 2, techs
    policy.reset()
    assert (policy.memory.vector()[:len(ACTIONS)] == 0).all()
    sampled = BCPolicy(model, seed=1)
    assert any("TECH_UP" in sampled(f) for _ in range(30)) and sampled(f).get("TRAIN_WORKER") == 3

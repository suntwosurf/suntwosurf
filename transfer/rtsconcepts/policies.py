"""Macro policies: features (spec.FEATURES) -> how many of each macro action
(spec.ACTIONS) in the next 10 s. The same policy runs in either game."""

from __future__ import annotations

import random

from .spec import ACTIONS, FEATURES

F = {name: i for i, name in enumerate(FEATURES)}


class RulePolicy:
    """Hand-written reference: a standard opening (supply, economy,
    production, a second economy building, a second production building),
    two gatherers per economy building, an army once that stands, defences
    when attacked, attack with a big army, defend when threatened."""

    name = "rules"

    def __init__(self, attack_value: float = 80, attack_after_min: float = 10):
        self.attack_value, self.attack_after_min = attack_value, attack_after_min

    def __call__(self, f: list[float], st: dict | None = None) -> dict[str, int]:
        g = {name: f[i] for name, i in F.items()}
        a: dict[str, int] = {}
        threatened = g["threat_home"] > 0
        eco_target = 2 if g["t_min"] < 8 else 3
        opening_done = g["eco_buildings"] >= 2 and g["production_buildings"] >= 2
        if g["supply_headroom"] < 0.2:
            a["BUILD_SUPPLY"] = 1
        elif g["eco_buildings"] < 1:
            a["BUILD_ECONOMY"] = 1
        elif g["production_buildings"] < 1:
            a["BUILD_PRODUCTION"] = 1
        elif g["eco_buildings"] < 2:
            a["BUILD_ECONOMY"] = 1
        elif g["production_buildings"] < 2:
            a["BUILD_PRODUCTION"] = 1
        elif g["eco_buildings"] < eco_target:
            a["BUILD_ECONOMY"] = 1
        elif g["t_min"] > 8 and g["tech"] < 0.5:
            a["TECH_UP"] = 1
        if (threatened or g["t_min"] > 6) and g["defenses"] < 2:
            a["BUILD_DEFENSE"] = 1
        if g["workers"] < 2 * g["eco_buildings"] and g["production_buildings"] >= 2:
            a["TRAIN_WORKER"] = 1
        if threatened or (opening_done and g["stock"] > 3):
            a["TRAIN_ARMY"] = 3
        if threatened:
            a["DEFEND"] = 1
        elif g["army_value"] > self.attack_value and g["t_min"] > self.attack_after_min:
            a["ATTACK"] = 1
        return a


class RandomPolicy:
    """Each macro action with the same chance each step."""

    name = "random"

    def __init__(self, p: float = 0.3, seed: int | None = None):
        self.p, self.rng = p, random.Random(seed)

    def __call__(self, f, st=None):
        return {a: (3 if a in ("TRAIN_ARMY", "TRAIN_WORKER") else 1) for a in ACTIONS if self.rng.random() < self.p}


class BCPolicy:
    """The imitation model (bc.py): take an action when its probability is
    above ``threshold``; units in batches of ``batch``."""

    name = "bc"

    def __init__(self, model, threshold: float = 0.5, batch: int = 3):
        self.model, self.threshold, self.batch = model, threshold, batch

    def __call__(self, f, st=None):
        import numpy as np

        p = self.model.predict(np.array([f], dtype=np.float64))[0]
        return {a: (self.batch if a.startswith("TRAIN") else 1) for a, pa in zip(ACTIONS, p) if pa > self.threshold}

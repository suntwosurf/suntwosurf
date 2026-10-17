"""Concept labels (spec.CONCEPTS): fixed formulas, each a number 0..1.

A label may use what the student doesn't see: the privileged values (the
true enemy army and income) and the teacher's decisions of the last minute.
The student learns to predict the labels from what it does see, and its
policy only sees those predictions (student.py).

The constants are in the shared units (money / 100, about one basic
soldier), so the same formulas can label OpenRA games.
"""

from __future__ import annotations

import numpy as np

from .spec import ACTIONS, CONCEPTS, FEATURES, MEMORY_STEPS, PRIVILEGED

F = {name: i for i, name in enumerate(FEATURES)}
P = {name: i for i, name in enumerate(PRIVILEGED)}

# What one busy production building (or base) spends per minute. The 0 A.D.
# recordings: median 4.7 over steps with production more than 80 % busy.
CAPACITY_PER_BUILDING = 5.0
SCARCE_BELOW_MINUTES = 2.0     # stock + income below 2 minutes of production capacity: scarce
# more than a minute of capacity in stock is piling up; full bottleneck at 3
# (0 A.D. recordings: median 0.9 minutes, 95th percentile 2.4)
BOTTLENECK_FROM_MINUTES, BOTTLENECK_FULL_MINUTES = 1.0, 3.0
SUPPLY_BLOCK_BELOW = 0.1       # headroom below 10 %: blocked
# rough cost (money / 100) of one decision, for ECONOMIC_INVESTMENT
COST = {"TRAIN_WORKER": 0.5, "TRAIN_ARMY": 1.0, "BUILD_ECONOMY": 1.0, "EXPAND": 6.0,
        "BUILD_PRODUCTION": 3.0, "BUILD_DEFENSE": 3.0}
ECONOMY = ["TRAIN_WORKER", "BUILD_ECONOMY", "EXPAND"]
MILITARY = ["TRAIN_ARMY", "BUILD_PRODUCTION", "BUILD_DEFENSE"]


def _share(own: np.ndarray, other: np.ndarray, at_least: float) -> np.ndarray:
    """own / (own + other); 0.5 while both are about zero."""
    total = own + other
    return np.where(total >= at_least, own / np.maximum(total, 1e-9), 0.5)


def labels(traj) -> np.ndarray:
    """The concept labels at every step of a recorded trajectory, shape (T, len(CONCEPTS))."""
    x, z = np.asarray(traj.features, np.float64), np.asarray(traj.privileged, np.float64)
    g = {name: x[:, i] for name, i in F.items()}
    capacity = CAPACITY_PER_BUILDING * np.maximum(1.0, g["production_buildings"] + g["bases"])

    scarcity = 1 - (g["stock"] + g["income"]) / (SCARCE_BELOW_MINUTES * capacity)
    bottleneck = (g["stock"] / capacity - BOTTLENECK_FROM_MINUTES) / (BOTTLENECK_FULL_MINUTES - BOTTLENECK_FROM_MINUTES)
    supply = 1 - g["supply_headroom"] / SUPPLY_BLOCK_BELOW

    # the teacher's decisions in the last minute before each state (the
    # decisions after state k are actions[k])
    a = np.asarray(traj.actions, np.float64)
    upto = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(a, 0)])  # upto[k]: decisions before state k
    k = np.arange(len(a))
    window = upto[k] - upto[np.maximum(0, k - MEMORY_STEPS)]
    spent = {name: window[:, ACTIONS.index(name)] * COST[name] for name in ECONOMY + MILITARY}
    investment = _share(sum(spent[n] for n in ECONOMY), sum(spent[n] for n in MILITARY), 1e-9)

    military = _share(g["army_value"], z[:, P["enemy_army_value"]], 1.0)
    economic = _share(g["income"], z[:, P["enemy_income"]], 0.5)
    threat = g["threat_home"]
    pressure = np.where(threat > 0, (threat + g["lost_value"]) / (threat + g["lost_value"] + g["army_value"] + 1), 0.0)

    out = np.stack([scarcity, bottleneck, supply, investment, military, economic, pressure], axis=1)
    assert out.shape[1] == len(CONCEPTS)
    return np.clip(out, 0.0, 1.0)


def stack_labels(trajs) -> np.ndarray:
    if not trajs:
        return np.zeros((0, len(CONCEPTS)))
    return np.concatenate([labels(t) for t in trajs])

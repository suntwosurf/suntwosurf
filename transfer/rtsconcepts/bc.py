"""Behaviour cloning (BC) baseline: from the shared features, predict which
macro decisions the teacher takes in the next 10 s (one yes/no per action).

A small neural network in plain numpy, so it runs anywhere and its weights
can later be written into the game (like params.js). This is the "no
concepts" student (C in DESIGN.md); the concept bottleneck comes on top.

Honest scoring: rare decisions (EXPAND, TECH_UP) are almost always "no", so
"always say no" already agrees 99 % of the time on them. Every number is
shown next to that always-the-majority answer, and balanced accuracy (the
mean of the hit rates on "yes" and on "no") is the fair measure.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .dataset import Trajectory, split_by_game, stack
from .spec import ACTIONS, FEATURES, SPEC_VERSION

# counts and amounts grow without bound: learn on log(1 + x)
LOG_FEATURES = [f for f in FEATURES if f not in {"supply_headroom", "production_busy", "tech", "explored"}]
_LOG_IDX = np.array([FEATURES.index(f) for f in LOG_FEATURES])


def transform(x: np.ndarray) -> np.ndarray:
    x = np.array(x, dtype=np.float64)
    x[:, _LOG_IDX] = np.log1p(np.maximum(x[:, _LOG_IDX], 0.0))
    return x


class MLP:
    """ReLU hidden layers, one logit per action."""

    def __init__(self, sizes: list[int], rng: np.random.Generator):
        self.W = [rng.normal(0, math.sqrt(2 / a), (a, b)) for a, b in zip(sizes, sizes[1:])]
        self.b = [np.zeros(b) for b in sizes[1:]]

    def forward(self, x: np.ndarray, keep: bool = False):
        acts = [x]
        for i, (W, b) in enumerate(zip(self.W, self.b)):
            x = x @ W + b
            if i < len(self.W) - 1:
                x = np.maximum(x, 0.0)
            acts.append(x)
        return (x, acts) if keep else x

    def grads(self, acts: list[np.ndarray], g: np.ndarray):
        gW, gb = [None] * len(self.W), [None] * len(self.b)
        for i in reversed(range(len(self.W))):
            gW[i] = acts[i].T @ g
            gb[i] = g.sum(0)
            if i:
                g = (g @ self.W[i].T) * (acts[i] > 0)
        return gW, gb


def _bce(logits: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.maximum(logits, 0) - logits * y + np.log1p(np.exp(-np.abs(logits)))))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


class BCModel:
    def __init__(self, net: MLP, mean: np.ndarray, std: np.ndarray):
        self.net, self.mean, self.std = net, mean, std

    def logits(self, features: np.ndarray) -> np.ndarray:
        return self.net.forward((transform(features) - self.mean) / self.std)

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Probability of each action in the next 10 s, shape (N, len(ACTIONS))."""
        return _sigmoid(self.logits(features))

    def to_dict(self) -> dict:
        return {"kind": "bc", "v": SPEC_VERSION, "features": FEATURES, "actions": ACTIONS,
                "log_features": LOG_FEATURES, "mean": self.mean.tolist(), "std": self.std.tolist(),
                "layers": [{"W": W.tolist(), "b": b.tolist()} for W, b in zip(self.net.W, self.net.b)]}

    @classmethod
    def from_dict(cls, d: dict) -> "BCModel":
        if d.get("features") != FEATURES or d.get("actions") != ACTIONS:
            raise ValueError("model made for another feature/action list")
        net = MLP.__new__(MLP)
        net.W = [np.array(layer["W"]) for layer in d["layers"]]
        net.b = [np.array(layer["b"]) for layer in d["layers"]]
        return cls(net, np.array(d["mean"]), np.array(d["std"]))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")


def train(train_trajs: list[Trajectory], hidden: tuple[int, ...] = (64, 64), epochs: int = 30,
          batch: int = 1024, lr: float = 1e-3, seed: int = 0, log=lambda m: None) -> BCModel:
    """Adam on binary cross-entropy. 10 % of the training games are kept aside
    to stop when the network starts memorising (early stopping)."""
    rng = np.random.default_rng(seed)
    fit_trajs, val_trajs = split_by_game(train_trajs, holdout=0.1, seed=seed + 1)
    x, _, a = stack(fit_trajs)
    xv, _, av = stack(val_trajs)
    xt = transform(x)
    mean, std = xt.mean(0), xt.std(0) + 1e-6
    X, Y = (xt - mean) / std, (a > 0).astype(np.float64)
    Xv, Yv = (transform(xv) - mean) / std, (av > 0).astype(np.float64)

    batch = min(batch, max(64, len(X) // 50))  # small data: enough updates per pass
    net = MLP([X.shape[1], *hidden, Y.shape[1]], rng)
    params = net.W + net.b
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    best, best_params, bad, step = math.inf, None, 0, 0
    for epoch in range(epochs):
        order = rng.permutation(len(X))
        for i in range(0, len(X), batch):
            idx = order[i:i + batch]
            logits, acts = net.forward(X[idx], keep=True)
            g = (_sigmoid(logits) - Y[idx]) / logits.size
            gW, gb = net.grads(acts, g)
            step += 1
            for k, (p, gp) in enumerate(zip(params, gW + gb)):
                m[k] = 0.9 * m[k] + 0.1 * gp
                v[k] = 0.999 * v[k] + 0.001 * gp * gp
                p -= lr * (m[k] / (1 - 0.9 ** step)) / (np.sqrt(v[k] / (1 - 0.999 ** step)) + 1e-8)
        val = _bce(net.forward(Xv), Yv) if len(Xv) else _bce(net.forward(X), Y)
        log(f"  epoch {epoch + 1}: validation loss {val:.4f}")
        if val < best - 1e-4:
            best, best_params, bad = val, [p.copy() for p in params], 0
        else:
            bad += 1
            if bad >= 3:
                break
    if best_params is not None:
        for p, q in zip(params, best_params):
            p[...] = q
    return BCModel(net, mean, std)


def score(model: BCModel, trajs: list[Trajectory]) -> dict:
    """Agreement with the teacher on ``trajs``, per action and overall, next to
    what always giving the most common answer would get."""
    x, _, a = stack(trajs)
    y = a > 0
    pred = model.predict(x) > 0.5
    per = {}
    for j, name in enumerate(ACTIONS):
        yes = y[:, j]
        rate = float(yes.mean()) if len(yes) else 0.0
        hit_yes = float((pred[yes, j]).mean()) if yes.any() else float("nan")
        hit_no = float((~pred[~yes, j]).mean()) if (~yes).any() else float("nan")
        per[name] = {"rate": rate, "model": float((pred[:, j] == yes).mean()), "majority": max(rate, 1 - rate),
                     "balanced": float(np.nanmean([hit_yes, hit_no]))}
    majority_pred = y.mean(0) > 0.5
    return {
        "steps": int(len(y)), "games": len({t.game for t in trajs}), "per_action": per,
        "mean_model": float(np.mean([p["model"] for p in per.values()])),
        "mean_majority": float(np.mean([p["majority"] for p in per.values()])),
        "mean_balanced": float(np.nanmean([p["balanced"] for p in per.values()])),
        "all_right": float((pred == y).all(1).mean()), "all_right_majority": float((majority_pred == y).all(1).mean()),
    }


def report(s: dict) -> list[str]:
    lines = [f"imitation of the teacher on {s['games']} games it never saw ({s['steps']} steps):",
             "does the teacher take each decision in the next 10 s?", "",
             "decision           happens   model right   always-majority right   balanced"]
    for name, p in s["per_action"].items():
        lines.append(f"{name:18s} {p['rate'] * 100:6.1f} %   {p['model'] * 100:8.1f} %   {p['majority'] * 100:14.1f} %"
                     f"          {p['balanced'] * 100:5.1f} %")
    lines += [
        "",
        f"mean over decisions: model {s['mean_model'] * 100:.1f} %, always-majority {s['mean_majority'] * 100:.1f} %; "
        f"balanced {s['mean_balanced'] * 100:.1f} % (50 % = no better than guessing)",
        f"every decision of a step right: model {s['all_right'] * 100:.1f} %, "
        f"always-majority {s['all_right_majority'] * 100:.1f} %",
    ]
    return lines

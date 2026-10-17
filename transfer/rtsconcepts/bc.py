"""Behaviour cloning (BC) baseline: from the shared features and the memory
of its own recent decisions (memory.py), predict which macro decisions the
teacher takes in the next 10 s (one yes/no per action).

A small neural network in plain numpy, so it runs anywhere and its weights
can later be written into the game (like params.js). This is the "no
concepts" student (C in DESIGN.md); student.py adds the concept bottleneck.

Honest scoring: rare decisions (EXPAND, TECH_UP) are almost always "no", so
"always say no" already agrees 99 % of the time on them, and a model asked
"above 50 %?" never says yes to them even when it ranks the right moments
first. So the scores don't depend on a cut-off:
* AUC: the chance that a step where the teacher takes the decision gets a
  higher probability than one where it doesn't (0.5 = guessing, 1 = perfect);
* skill: how much better the probabilities are than always giving the
  decision's base rate, in log-loss (0 = no better, 100 % = perfect);
* balanced accuracy when "yes" means "more likely than usual" (above the
  base rate): the mean of the hit rates on "yes" and on "no".
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .dataset import Trajectory, split_by_game, stack
from .memory import stack_memory
from .spec import ACTIONS, FEATURES, MEMORY, SPEC_VERSION

# counts and amounts grow without bound: learn on log(1 + x)
LOG_FEATURES = [f for f in FEATURES if f not in {"supply_headroom", "production_busy", "tech", "explored"}]
_LOG_IDX = np.array([FEATURES.index(f) for f in LOG_FEATURES])


def transform(x: np.ndarray) -> np.ndarray:
    x = np.array(x, dtype=np.float64)
    x[:, _LOG_IDX] = np.log1p(np.maximum(x[:, _LOG_IDX], 0.0))
    return x


class MLP:
    """ReLU hidden layers, linear outputs."""

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

    def grads(self, acts: list[np.ndarray], g: np.ndarray, wrt_input: bool = False):
        """Gradients of the weights and biases for the output gradient ``g``;
        with ``wrt_input`` also of the input."""
        gW, gb = [None] * len(self.W), [None] * len(self.b)
        for i in reversed(range(len(self.W))):
            gW[i] = acts[i].T @ g
            gb[i] = g.sum(0)
            if i:
                g = (g @ self.W[i].T) * (acts[i] > 0)
        if wrt_input:
            return gW, gb, g @ self.W[0].T
        return gW, gb

    def to_list(self) -> list[dict]:
        return [{"W": W.tolist(), "b": b.tolist()} for W, b in zip(self.W, self.b)]

    @classmethod
    def from_list(cls, layers: list[dict]) -> "MLP":
        net = cls.__new__(cls)
        net.W = [np.array(layer["W"]) for layer in layers]
        net.b = [np.array(layer["b"]) for layer in layers]
        return net


def _bce(logits: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.maximum(logits, 0) - logits * y + np.log1p(np.exp(-np.abs(logits)))))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def fit(params: list[np.ndarray], grad_fn, val_fn, n: int, epochs: int, batch: int, lr: float,
        rng: np.random.Generator, log) -> None:
    """Adam over minibatches of ``n`` rows. ``grad_fn(rows)`` returns the
    gradients in ``params`` order; training stops once ``val_fn()`` (the loss on
    games kept aside) hasn't improved for 3 passes, and keeps the best
    parameters (early stopping)."""
    batch = min(batch, max(64, n // 50))  # small data: enough updates per pass
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    best, best_params, bad, step = math.inf, None, 0, 0
    for epoch in range(epochs):
        order = rng.permutation(n)
        for i in range(0, n, batch):
            step += 1
            for k, (p, gp) in enumerate(zip(params, grad_fn(order[i:i + batch]))):
                m[k] = 0.9 * m[k] + 0.1 * gp
                v[k] = 0.999 * v[k] + 0.001 * gp * gp
                p -= lr * (m[k] / (1 - 0.9 ** step)) / (np.sqrt(v[k] / (1 - 0.999 ** step)) + 1e-8)
        val = val_fn()
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


def model_inputs(trajs: list[Trajectory], memory: bool) -> np.ndarray:
    """The model's input columns before standardising: log features, then the memory."""
    x = transform(stack(trajs)[0])
    return np.hstack([x, stack_memory(trajs)]) if memory else x


class BCModel:
    def __init__(self, net: MLP, mean: np.ndarray, std: np.ndarray, memory: bool = True,
                 rate: np.ndarray | None = None):
        self.net, self.mean, self.std, self.memory = net, mean, std, memory
        self.rate = np.full(len(ACTIONS), 0.5) if rate is None else np.asarray(rate, np.float64)

    def _std_inputs(self, features: np.ndarray, memory: np.ndarray | None) -> np.ndarray:
        x = transform(features)
        if self.memory:
            if memory is None:
                raise ValueError("this model needs the memory of its own decisions (memory.Memory)")
            x = np.hstack([x, np.asarray(memory, np.float64).reshape(len(x), -1)])
        return (x - self.mean) / self.std

    def predict(self, features: np.ndarray, memory: np.ndarray | None = None) -> np.ndarray:
        """Probability of each action in the next 10 s, shape (N, len(ACTIONS))."""
        return _sigmoid(self.net.forward(self._std_inputs(features, memory)))

    def predict_trajs(self, trajs: list[Trajectory]) -> np.ndarray:
        return _sigmoid(self.net.forward((model_inputs(trajs, self.memory) - self.mean) / self.std))

    def to_dict(self) -> dict:
        return {"kind": "bc", "v": SPEC_VERSION, "features": FEATURES, "actions": ACTIONS,
                "memory": MEMORY if self.memory else [], "log_features": LOG_FEATURES,
                "mean": self.mean.tolist(), "std": self.std.tolist(), "rate": self.rate.tolist(),
                "layers": self.net.to_list()}

    @classmethod
    def from_dict(cls, d: dict) -> "BCModel":
        if d.get("features") != FEATURES or d.get("actions") != ACTIONS or d.get("memory", []) not in ([], MEMORY):
            raise ValueError("model made for another feature/action/memory list")
        return cls(MLP.from_list(d["layers"]), np.array(d["mean"]), np.array(d["std"]),
                   memory=bool(d.get("memory")), rate=d.get("rate"))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")


def train(train_trajs: list[Trajectory], hidden: tuple[int, ...] = (64, 64), epochs: int = 30,
          batch: int = 1024, lr: float = 1e-3, seed: int = 0, memory: bool = True, log=lambda m: None) -> BCModel:
    """Adam on binary cross-entropy. 10 % of the training games are kept aside
    to stop when the network starts memorising (early stopping)."""
    rng = np.random.default_rng(seed)
    fit_trajs, val_trajs = split_by_game(train_trajs, holdout=0.1, seed=seed + 1)
    xt, xv = model_inputs(fit_trajs, memory), model_inputs(val_trajs, memory)
    mean, std = xt.mean(0), xt.std(0) + 1e-6
    X, Y = (xt - mean) / std, (stack(fit_trajs)[2] > 0).astype(np.float64)
    Xv, Yv = (xv - mean) / std, (stack(val_trajs)[2] > 0).astype(np.float64)

    net = MLP([X.shape[1], *hidden, Y.shape[1]], rng)

    def grad_fn(rows):
        logits, acts = net.forward(X[rows], keep=True)
        gW, gb = net.grads(acts, (_sigmoid(logits) - Y[rows]) / logits.size)
        return gW + gb

    def val_fn():
        return _bce(net.forward(Xv), Yv) if len(Xv) else _bce(net.forward(X), Y)

    fit(net.W + net.b, grad_fn, val_fn, len(X), epochs, batch, lr, rng, log)
    return BCModel(net, mean, std, memory=memory, rate=Y.mean(0))


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """Area under the ROC curve (ties count half); nan without both answers."""
    y = np.asarray(y, bool)
    pos, neg = int(y.sum()), int((~y).sum())
    if not pos or not neg:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sp = np.asarray(p)[order]
    # average ranks over ties
    _, first, counts = np.unique(sp, return_index=True, return_counts=True)
    avg = first + (counts + 1) / 2
    ranks[order] = np.repeat(avg, counts)
    return float((ranks[y].sum() - pos * (pos + 1) / 2) / (pos * neg))


def _logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def score_probs(p: np.ndarray, y: np.ndarray, rate: np.ndarray) -> dict:
    """Scores of predicted probabilities ``p`` (N, A) against the teacher's
    decisions ``y`` (N, A, yes/no), with ``rate`` the base rates of the training data."""
    per = {}
    for j, name in enumerate(ACTIONS):
        yes, pj, r = y[:, j], p[:, j], float(rate[j])
        happens = float(yes.mean()) if len(yes) else 0.0
        more = pj > r if r > 0 else np.zeros(len(pj), bool)  # "more likely than usual"
        hit_yes = float(more[yes].mean()) if yes.any() else float("nan")
        hit_no = float((~more[~yes]).mean()) if (~yes).any() else float("nan")
        base = _logloss(np.full(len(yes), min(max(r, 1e-4), 1 - 1e-4)), yes)
        per[name] = {
            "rate": happens, "auc": auc(pj, yes),
            "skill": 1 - _logloss(pj, yes) / base if yes.any() and (~yes).any() else float("nan"),
            "balanced": float(np.nanmean([hit_yes, hit_no])) if yes.any() or (~yes).any() else float("nan"),
            "model": float(((pj > 0.5) == yes).mean()), "majority": max(happens, 1 - happens),
        }
    rare = [p_["auc"] for p_ in per.values() if p_["rate"] < 0.05 and p_["auc"] == p_["auc"]]
    return {
        "steps": int(len(y)), "per_action": per,
        "mean_auc": float(np.nanmean([p_["auc"] for p_ in per.values()])),
        "mean_auc_rare": float(np.mean(rare)) if rare else float("nan"),
        "mean_skill": float(np.nanmean([p_["skill"] for p_ in per.values()])),
        "mean_balanced": float(np.nanmean([p_["balanced"] for p_ in per.values()])),
        "mean_model": float(np.mean([p_["model"] for p_ in per.values()])),
        "mean_majority": float(np.mean([p_["majority"] for p_ in per.values()])),
    }


def score(model, trajs: list[Trajectory]) -> dict:
    """How well ``model`` (anything with ``predict_trajs`` and ``rate``)
    predicts the teacher's decisions on ``trajs``."""
    y = stack(trajs)[2] > 0
    s = score_probs(model.predict_trajs(trajs), y, model.rate)
    s["games"] = len({t.game for t in trajs})
    return s


def _pct(v: float, width: int = 5) -> str:
    return f"{v * 100:{width}.1f} %" if v == v else f"{'-':>{width}}  "


def report(s: dict, title: str = "imitation of the teacher") -> list[str]:
    lines = [f"{title} on {s['games']} games it never saw ({s['steps']} steps):",
             "does the teacher take each decision in the next 10 s?", "",
             "decision           happens    AUC     skill    balanced"]
    for name, p in s["per_action"].items():
        auc_ = f"{p['auc']:.3f}" if p["auc"] == p["auc"] else "  -  "
        lines.append(f"{name:18s} {_pct(p['rate'])}   {auc_}  {_pct(p['skill'])}  {_pct(p['balanced'])}")
    lines += [
        "",
        f"mean AUC {s['mean_auc']:.3f} (rare decisions, under 5 % of steps: {s['mean_auc_rare']:.3f}); "
        f"skill {s['mean_skill'] * 100:.1f} %; balanced {s['mean_balanced'] * 100:.1f} %",
        "  AUC: 0.5 = guessing, 1 = perfect ranking. skill: log-loss better than the base rate.",
        "  balanced: \"yes\" when more likely than usual; 50 % = no better than guessing.",
        f"agreement with a 50 % cut-off: model {s['mean_model'] * 100:.1f} %, always-majority "
        f"{s['mean_majority'] * 100:.1f} % (rare decisions make this look good for anyone)",
    ]
    return lines

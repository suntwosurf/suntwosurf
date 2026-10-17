"""The concept student (DESIGN.md: the shared core of students A and B).

    features + memory ──▶ encoder ──▶ concepts (0..1, supervised) ──┐
                                                                     ├──▶ policy ──▶ decisions
    memory, a few raw features (POLICY_FEATURES) ───────────────────┘

The encoder learns to predict the concept labels (concepts.py) from what the
student sees; the policy sees only those predictions, the memory and a few
raw features. So all it knows about the game state passes through numbers
whose meaning is fixed and checkable in either game. Both parts train
together: the decisions' cross-entropy plus ``concept_weight`` times the
concepts'.

The same numpy MLPs as bc.py, saved as JSON for use in the game later.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .bc import MLP, _bce, _sigmoid, fit, model_inputs, transform
from .concepts import stack_labels
from .dataset import Trajectory, split_by_game, stack
from .spec import ACTIONS, CONCEPTS, FEATURES, MEMORY, SPEC_VERSION

POLICY_FEATURES = ["t_min", "tech", "bases"]


class ConceptStudent:
    def __init__(self, encoder: MLP, policy: MLP, mean: np.ndarray, std: np.ndarray, rate: np.ndarray,
                 policy_features: list[str] = POLICY_FEATURES):
        self.encoder, self.policy, self.mean, self.std = encoder, policy, mean, std
        self.rate = np.asarray(rate, np.float64)
        self.policy_features = list(policy_features)
        # policy's raw columns of the standardised input: those features, then the whole memory
        self._keep = np.array([FEATURES.index(f) for f in self.policy_features]
                              + list(range(len(FEATURES), len(FEATURES) + len(MEMORY))), dtype=int)

    def _forward(self, X: np.ndarray, keep: bool = False):
        concept_logits, enc_acts = self.encoder.forward(X, keep=True)
        c = _sigmoid(concept_logits)
        action_logits, pol_acts = self.policy.forward(np.hstack([c, X[:, self._keep]]), keep=True)
        return (concept_logits, c, action_logits, enc_acts, pol_acts) if keep else (c, _sigmoid(action_logits))

    def _std(self, features: np.ndarray, memory: np.ndarray) -> np.ndarray:
        x = np.hstack([transform(features), np.asarray(memory, np.float64).reshape(len(features), -1)])
        return (x - self.mean) / self.std

    def concepts(self, features: np.ndarray, memory: np.ndarray) -> np.ndarray:
        """Predicted concepts, shape (N, len(CONCEPTS))."""
        return self._forward(self._std(features, memory))[0]

    def predict(self, features: np.ndarray, memory: np.ndarray | None = None) -> np.ndarray:
        """Probability of each action in the next 10 s, shape (N, len(ACTIONS))."""
        if memory is None:
            raise ValueError("the student needs the memory of its own decisions (memory.Memory)")
        return self._forward(self._std(features, memory))[1]

    def _std_trajs(self, trajs: list[Trajectory]) -> np.ndarray:
        return (model_inputs(trajs, memory=True) - self.mean) / self.std

    def predict_trajs(self, trajs: list[Trajectory]) -> np.ndarray:
        return self._forward(self._std_trajs(trajs))[1]

    def concepts_trajs(self, trajs: list[Trajectory]) -> np.ndarray:
        return self._forward(self._std_trajs(trajs))[0]

    def to_dict(self) -> dict:
        return {"kind": "concept_student", "v": SPEC_VERSION, "features": FEATURES, "actions": ACTIONS,
                "memory": MEMORY, "concepts": CONCEPTS, "policy_features": self.policy_features,
                "mean": self.mean.tolist(), "std": self.std.tolist(), "rate": self.rate.tolist(),
                "encoder": self.encoder.to_list(), "policy": self.policy.to_list()}

    @classmethod
    def from_dict(cls, d: dict) -> "ConceptStudent":
        if d.get("kind") != "concept_student" or d.get("features") != FEATURES or d.get("actions") != ACTIONS \
                or d.get("memory") != MEMORY or d.get("concepts") != CONCEPTS:
            raise ValueError("model made for another feature/action/memory/concept list")
        return cls(MLP.from_list(d["encoder"]), MLP.from_list(d["policy"]), np.array(d["mean"]),
                   np.array(d["std"]), np.array(d["rate"]), d["policy_features"])

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")


def train(train_trajs: list[Trajectory], hidden: tuple[int, ...] = (64,), policy_hidden: tuple[int, ...] = (64,),
          concept_weight: float = 1.0, policy_features: list[str] = POLICY_FEATURES, epochs: int = 30,
          batch: int = 1024, lr: float = 1e-3, seed: int = 0, log=lambda m: None) -> ConceptStudent:
    rng = np.random.default_rng(seed)
    fit_trajs, val_trajs = split_by_game(train_trajs, holdout=0.1, seed=seed + 1)
    xt, xv = model_inputs(fit_trajs, memory=True), model_inputs(val_trajs, memory=True)
    mean, std = xt.mean(0), xt.std(0) + 1e-6
    X, Xv = (xt - mean) / std, (xv - mean) / std
    Y, Yv = (stack(fit_trajs)[2] > 0).astype(np.float64), (stack(val_trajs)[2] > 0).astype(np.float64)
    C, Cv = stack_labels(fit_trajs), stack_labels(val_trajs)

    k = len(CONCEPTS)
    encoder = MLP([X.shape[1], *hidden, k], rng)
    n_raw = len(policy_features) + len(MEMORY)
    policy = MLP([k + n_raw, *policy_hidden, len(ACTIONS)], rng)
    model = ConceptStudent(encoder, policy, mean, std, Y.mean(0), policy_features)

    def grad_fn(rows):
        concept_logits, c, action_logits, enc_acts, pol_acts = model._forward(X[rows], keep=True)
        g_actions = (_sigmoid(action_logits) - Y[rows]) / action_logits.size
        pW, pb, g_in = policy.grads(pol_acts, g_actions, wrt_input=True)
        g_concepts = g_in[:, :k] * c * (1 - c) + concept_weight * (c - C[rows]) / concept_logits.size
        eW, eb = encoder.grads(enc_acts, g_concepts)
        return eW + pW + eb + pb

    def loss(Xs, Ys, Cs):
        concept_logits, _, action_logits, _, _ = model._forward(Xs, keep=True)
        return _bce(action_logits, Ys) + concept_weight * (_bce(concept_logits, Cs) - _bce_floor(Cs))

    def val_fn():
        return loss(Xv, Yv, Cv) if len(Xv) else loss(X, Y, C)

    fit(encoder.W + policy.W + encoder.b + policy.b, grad_fn, val_fn, len(X), epochs, batch, lr, rng, log)
    return model


def _bce_floor(c: np.ndarray) -> float:
    """The cross-entropy of soft labels with themselves (its lowest value), so
    the concept part of the loss reads 0 when the labels are matched exactly."""
    c = np.clip(c, 1e-9, 1 - 1e-9)
    return float(np.mean(-(c * np.log(c) + (1 - c) * np.log(1 - c))))


def score_concepts(model: ConceptStudent, trajs: list[Trajectory]) -> dict:
    """Per concept: the labels' mean, the mean error of the prediction, and the
    share of the labels' variation it explains (R², 0 = no better than always
    the mean)."""
    labels, pred = stack_labels(trajs), model.concepts_trajs(trajs)
    out = {}
    for j, name in enumerate(CONCEPTS):
        y, p = labels[:, j], pred[:, j]
        var = float(((y - y.mean()) ** 2).mean())
        out[name] = {"mean": float(y.mean()), "error": float(np.abs(p - y).mean()),
                     "explained": 1 - float(((p - y) ** 2).mean()) / var if var > 0 else float("nan")}
    return out


def report_concepts(s: dict) -> list[str]:
    lines = ["concept                  label mean   prediction error   explained (R²)"]
    for name, c in s.items():
        explained = f"{c['explained'] * 100:6.1f} %" if c["explained"] == c["explained"] else "     -  "
        lines.append(f"{name:24s} {c['mean']:8.2f}     {c['error']:10.3f}         {explained}")
    return lines

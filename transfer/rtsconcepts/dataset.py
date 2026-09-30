"""Teacher recordings (``zadbot record``: one .jsonl file per game) as arrays.

A game file has one line about the game, then the recorder's lines: per
player a header and one step every 10 s of game time. A step's ``a`` holds
what the teacher decided since the previous step, so the decisions that
follow the state of step k are in step k + 1's ``a``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .spec import ACTIONS, FEATURES, PRIVILEGED, SPEC_VERSION


class RecordingError(ValueError):
    pass


@dataclass
class Trajectory:
    game: str
    player: int
    civ: str
    difficulty: int
    behavior: str
    won: bool | None       # None: the game has no winner
    reason: str | None     # conquest | time_limit
    times: np.ndarray      # (T,) seconds of game time
    features: np.ndarray   # (T, len(FEATURES))
    privileged: np.ndarray  # (T, len(PRIVILEGED))
    actions: np.ndarray    # (T, len(ACTIONS)): decisions in the 10 s after each state

    def __len__(self) -> int:
        return len(self.times)


def load_game(path: str | Path) -> list[Trajectory]:
    """The trajectories of one recorded game (one per recording player)."""
    path = Path(path)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or "game" not in lines[0]:
        raise RecordingError(f"{path}: not a zadbot recording (no game line first)")
    game = lines[0]["game"]
    headers, steps = {}, {}
    for r in lines[1:]:
        if r.get("header"):
            _check_header(path, r)
            headers[r["p"]] = r
        elif "t" in r:
            steps.setdefault(r["p"], []).append(r)
    out = []
    for p, rows in sorted(steps.items()):
        if p not in headers or len(rows) < 2:
            continue
        rows.sort(key=lambda r: r["t"])
        h = headers[p]
        feats = np.array([r["f"] for r in rows[:-1]], dtype=np.float32)
        priv = np.array([r["x"] for r in rows[:-1]], dtype=np.float32)
        acts = np.array([[r.get("a", {}).get(a, 0) for a in ACTIONS] for r in rows[1:]], dtype=np.float32)
        winners = game.get("winners") or []
        out.append(Trajectory(
            game=path.stem, player=p, civ=h.get("civ", ""), difficulty=int(h.get("difficulty", 3)),
            behavior=h.get("behavior", ""), won=(p in winners) if winners else None, reason=game.get("reason"),
            times=np.array([r["t"] for r in rows[:-1]], dtype=np.float32), features=feats, privileged=priv,
            actions=acts,
        ))
    return out


def _check_header(path: Path, h: dict) -> None:
    if h.get("v") != SPEC_VERSION or h.get("features") != FEATURES or h.get("privileged") != PRIVILEGED \
            or h.get("actions") != ACTIONS:
        raise RecordingError(f"{path}: recorded with another feature/action list (version {h.get('v')}, "
                             f"this code has version {SPEC_VERSION}): record again with this zadbot")


def load_dir(directory: str | Path) -> list[Trajectory]:
    return [t for f in sorted(Path(directory).glob("*.jsonl")) for t in load_game(f)]


def split_by_game(trajs: list[Trajectory], holdout: float = 0.2, seed: int = 0) -> tuple[list, list]:
    """Train/held-out split by whole games, so both players of a game stay together."""
    games = sorted({t.game for t in trajs})
    rng = np.random.default_rng(seed)
    held = set(rng.choice(games, size=max(1, round(holdout * len(games))), replace=False)) if len(games) > 1 else set()
    return [t for t in trajs if t.game not in held], [t for t in trajs if t.game in held]


def stack(trajs: list[Trajectory]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """All steps of ``trajs``: features (N, F), privileged (N, P), actions (N, A)."""
    if not trajs:
        return (np.zeros((0, len(FEATURES)), np.float32), np.zeros((0, len(PRIVILEGED)), np.float32),
                np.zeros((0, len(ACTIONS)), np.float32))
    return (np.concatenate([t.features for t in trajs]), np.concatenate([t.privileged for t in trajs]),
            np.concatenate([t.actions for t in trajs]))

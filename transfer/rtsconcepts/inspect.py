"""A first look at recordings: are the values sane, and how often does the
teacher make each decision? Catches encoder bugs before any training."""

from __future__ import annotations

import numpy as np

from .dataset import Trajectory, stack
from .spec import ACTIONS, FEATURES

# features that can't be negative, and those that are shares (0..1)
SHARES = {"supply_headroom", "production_busy", "tech", "explored"}


def inspect(trajs: list[Trajectory]) -> list[str]:
    if not trajs:
        return ["no recordings"]
    x, _, a = stack(trajs)
    games = {t.game for t in trajs}
    lines = [f"{len(games)} games, {len(trajs)} player recordings, {len(x)} steps of 10 s "
             f"(median game {np.median([t.times[-1] for t in trajs]) / 60:.0f} game-min)"]

    decided = [t for t in trajs if t.won is not None]
    if decided:
        by_reason: dict[str, int] = {}
        for t in trajs:
            if t.won:
                by_reason[t.reason or "?"] = by_reason.get(t.reason or "?", 0) + 1
        lines.append("games won by " + ", ".join(f"{k} {v}" for k, v in sorted(by_reason.items())))
        lines.append("win rate by difficulty: " + ", ".join(
            f"{d}: {np.mean([t.won for t in decided if t.difficulty == d]) * 100:.0f} % of "
            f"{sum(t.difficulty == d for t in decided)}"
            for d in sorted({t.difficulty for t in decided})))

    lines.append("")
    lines.append("feature                  p5      median  p95     max     problems")
    for i, name in enumerate(FEATURES):
        col = x[:, i]
        problems = []
        if not np.isfinite(col).all():
            problems.append(f"{(~np.isfinite(col)).sum()} not a number")
        fin = col[np.isfinite(col)]
        if (fin < 0).any():
            problems.append(f"{(fin < 0).sum()} negative")
        if name in SHARES and (fin > 1.0001).any():
            problems.append(f"{(fin > 1.0001).sum()} above 1")
        if fin.size and fin.min() == fin.max():
            problems.append("never changes")
        p5, med, p95 = np.percentile(fin, [5, 50, 95]) if fin.size else (np.nan,) * 3
        lines.append(f"{name:22s} {p5:7.2f} {med:7.2f} {p95:7.2f} {fin.max() if fin.size else np.nan:7.1f}  "
                     + ("; ".join(problems) or "ok"))

    lines.append("")
    lines.append("decision           steps with it   mean count when taken")
    for j, name in enumerate(ACTIONS):
        col = a[:, j]
        taken = col > 0
        mean = col[taken].mean() if taken.any() else 0.0
        lines.append(f"{name:18s} {taken.mean() * 100:8.1f} %      {mean:6.1f}")
    lines.append(f"{'(none)':18s} {(a.sum(1) == 0).mean() * 100:8.1f} %")
    return lines

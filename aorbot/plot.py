"""Optional matplotlib figure of a learning session (pip install matplotlib)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .episode import EpisodeResult
from .learner import SpeedProfileLearner
from .line import RacingLine


def plot_learning(path: str | Path, line: RacingLine, learner: SpeedProfileLearner,
                  results: list[EpisodeResult], title: str = "", road_half_width: float | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_map, ax_hist, ax_speed) = plt.subplots(1, 3, figsize=(17, 5.5), gridspec_kw={"width_ratios": [1.2, 1, 1.3]})
    fig.suptitle(title or learner.conditions.label())

    if road_half_width:
        n = line.normal_at(line.s)
        for side in (-1, 1):
            edge = line.xy + side * road_half_width * n
            ax_map.plot(edge[:, 0], edge[:, 1], color="0.75", lw=0.8)
    ax_map.plot(line.xy[:, 0], line.xy[:, 1], color="0.35", lw=1, ls="--", label="reference line")
    fails = [r for r in results if not r.finished]
    for r in fails:
        ax_map.plot(*line.point_at(r.end_s), "x", color="tab:red", ms=6)
    best = min((r for r in results if r.finished), key=lambda r: r.time, default=None)
    if best is not None and "x" in best.trace:
        ax_map.plot(best.trace["x"], best.trace["y"], color="tab:blue", lw=1.4, label=f"best run {best.time:.2f}s")
    ax_map.plot(*line.xy[0], "o", color="tab:green", label="start")
    ax_map.plot(*line.xy[-1], "s", color="black", label="finish")
    ax_map.plot([], [], "x", color="tab:red", label="failures")
    ax_map.set_aspect("equal")
    ax_map.legend(loc="best", fontsize=8)
    ax_map.set_title("stage")

    it = np.arange(len(results))
    fin = np.array([r.finished for r in results])
    ax_hist.plot(it, [r.progress / line.length * 100 for r in results], color="0.6", lw=1)
    ax_hist.scatter(it[~fin], [r.progress / line.length * 100 for r in results if not r.finished],
                    color="tab:red", marker="x", label="failed (distance reached)")
    ax_hist.set_ylabel("stage completed (%)")
    ax_hist.set_xlabel("attempt")
    ax_t = ax_hist.twinx()
    ax_t.scatter(it[fin], [r.time for r in results if r.finished], color="tab:blue", s=16, label="finished (time)")
    ax_t.set_ylabel("stage time (s)")
    ax_hist.set_title("attempts")
    h1, l1 = ax_hist.get_legend_handles_labels()
    h2, l2 = ax_t.get_legend_handles_labels()
    ax_hist.legend(h1 + h2, l1 + l2, loc="center right", fontsize=8)

    v_first, _ = SpeedProfileLearner(line, learner.speed_cfg, learner.cfg, learner.max_offset,
                                     learner.conditions).targets()
    v_best, _ = learner.targets(best=True)
    ax_speed.plot(line.s, v_first * 3.6, color="0.6", lw=1, label="target, first attempt")
    ax_speed.plot(line.s, v_best * 3.6, color="tab:orange", lw=1.2, label="target, learned (best)")
    if best is not None and "speed" in best.trace:
        ax_speed.plot(best.trace["s"], best.trace["speed"] * 3.6, color="tab:blue", lw=1, label="driven, best run")
    ax_speed.set_xlabel("distance along stage (m)")
    ax_speed.set_ylabel("km/h")
    ax_speed.legend(fontsize=8)
    ax_speed.set_title("speed profile")

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)

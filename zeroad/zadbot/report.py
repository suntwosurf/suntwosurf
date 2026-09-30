"""Text reports: learning progress, what was learned, evaluation results."""

from __future__ import annotations

import math

from .params import describe


def learning_report(state: dict) -> list[str]:
    lines = [f"0 A.D. {state.get('game_version')}, generation {state['generation']}"]
    hist = state.get("history", [])
    if hist:
        lines.append("")
        lines.append("fitness: +1 = always wins by conquest, -1 = always loses, 0 = even with Petra")
        lines.append("centre = the learned bot's own matches that generation (few matches: noisy);")
        lines.append("avg5 = its average over the last 5 generations (read this one);")
        lines.append("best = the luckiest candidate, high even when nothing is learned")
        lines.append("")
        lines.append("gen  centre  avg5   best")
        for i, h in enumerate(hist):
            recent = [x["centre_fitness"] for x in hist[max(0, i - 4):i + 1]]
            avg = sum(recent) / len(recent)
            lines.append(f"{h['generation']:3d}  {h['centre_fitness']:+.2f}   {avg:+.2f}  {h['best_fitness']:+.2f}  {_bar(avg)}")
    lines.append("")
    lines.append("learned settings (the centre of the search):")
    lines += ["  " + line for line in describe(state["centre"])]
    return lines


def _bar(f: float, width: int = 20) -> str:
    if math.isnan(f):
        return ""
    n = round((f + 1) / 2 * width)
    return "[" + "#" * n + "." * (width - n) + "]"


def evaluation_report(results: dict[str, dict]) -> list[str]:
    lines = ["variant        wins  losses draws  conquest W/L  win rate  mean outcome  score margin  game-min"]
    for name, r in results.items():
        lines.append(
            f"{name:14s} {r['wins']:4d}  {r['losses']:6d} {r['draws']:5d}  {r['conquest_wins']:5d}/{r['conquest_losses']:<5d}"
            f"  {_pct(r['win_rate']):>8s}  {r['mean_outcome']:+12.2f}  {r['mean_score_margin']:+12.2f}  {r['mean_game_minutes']:8.1f}"
            + (f"   ({r['unfinished']} unfinished)" if r["unfinished"] else "")
        )
    return lines


def _pct(x: float) -> str:
    return "-" if math.isnan(x) else f"{100 * x:.0f} %"

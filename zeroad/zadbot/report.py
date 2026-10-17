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
    paired = {name: r["paired"] for name, r in results.items() if r.get("paired") and "diff" in r["paired"]}
    if len(paired) > 1:
        lines.append("")
        lines.append("each vs petra on the same maps: how many percentage points more often it wins")
        for name, pc in paired.items():
            d, h = 100 * pc["diff"], 100 * pc["range"]
            clear = "  a real difference" if pc["p"] < 0.05 else ""
            lines.append(f"  {name:12s} {d:+4.0f}  (95 % range {d - h:+.0f} to {d + h:+.0f}, {_p(pc['p'])}){clear}")
        return lines
    for name, r in results.items():
        pc = r.get("paired")
        if not pc:
            continue
        lines.append("")
        lines.append(f"{name} vs petra on the same maps: {name} won {pc['better']} that petra lost, "
                     f"petra won {pc['worse']} that {name} lost.")
        odds = f"about 1 in {1 / pc['p']:.0f}" if pc["p"] > 0 else "never"
        lines.append(f"  A split this uneven happens by pure chance {odds} times (p = {pc['p']:.2f}).")
        if "diff" in pc:
            d, h = 100 * pc["diff"], 100 * pc["range"]
            lines.append(f"  {name} wins {d:+.0f} percentage points more often than petra "
                         f"(95 % range {d - h:+.0f} to {d + h:+.0f}).")
            if pc["p"] < 0.05:
                verdict = "a real difference"
            elif h > 10:
                verdict = "not clear yet: more matches (--matches) would narrow the range"
            else:
                verdict = "no clear difference: any edge is smaller than the range above"
        else:
            verdict = "a real difference" if pc["p"] < 0.05 else "not clear"
        lines.append(f"  Verdict: {verdict}.")
    return lines


def _p(p: float) -> str:
    return "p < 0.01" if p < 0.01 else f"p = {p:.2f}"


def _pct(x: float) -> str:
    return "-" if math.isnan(x) else f"{100 * x:.0f} %"


def style_report(analysis: dict, results: dict[str, dict]) -> list[str]:
    """After ``evaluate --settings``: does the style matter, and would picking
    a style per civilisation help (see pipeline.style_analysis)?"""
    lines = ["", "Does the style change who wins?"]
    paired = {name: r["paired"] for name, r in results.items() if r.get("paired") and "diff" in r["paired"]}
    clear = {name: pc for name, pc in paired.items() if pc["p"] < 0.05}
    if clear:
        more = [n for n, pc in clear.items() if pc["diff"] > 0]
        less = [n for n, pc in clear.items() if pc["diff"] < 0]
        parts = ([_they(more, "wins", "win") + " clearly more often"] if more else []) + \
                ([_they(less, "wins", "win") + " clearly less often"] if less else [])
        lines.append(f"  Yes: {' and '.join(parts)} than petra, so these settings do change who wins.")
        if more:
            lines.append(f"  {_they(more, 'beats', 'beat')} plain Petra: worth using as the bot.")
    elif paired and max(100 * pc["range"] for pc in paired.values()) > 10:
        lines.append("  Not shown yet: no style differs clearly from petra, and the ranges above are wide;"
                     " more matches (--matches) would narrow them.")
    else:
        lines.append("  No: no style differs clearly from petra. Petra's settings barely change who wins,"
                     " so a selector on top of them has little to work with.")

    sel = analysis["selector"]
    d, h = 100 * sel["diff"], 100 * sel["range"]
    per_civ = analysis["matches_per_group"]
    lines.append("")
    lines.append("Would picking a style per civ help? (chosen on half of the maps, played on the other half)")
    lines.append(f"  a style per civ wins {_pct(analysis['per_group_win_rate'])}, "
                 f"one style for all civs {_pct(analysis['one_for_all_win_rate'])}"
                 + (f": {d:+.0f} points (95 % range {d - h:+.0f} to {d + h:+.0f}, {_p(sel['p'])})"
                    if sel["better"] + sel["worse"] else " (the same matches won)"))
    for i, c in enumerate(analysis["choices"]):
        other = {}
        for g, v in c["per_group"].items():
            if v != c["overall"]:
                other.setdefault(v, []).append(g)
        rest = "; ".join(f"{v} for {', '.join(gs)}" for v, gs in other.items())
        lines.append(f"  choices on half {i + 1}: {c['overall']} for {'the other civs' if rest else 'every civ'}"
                     + (f", {rest}" if rest else ""))
    if sel["p"] < 0.05 and sel["diff"] > 0:
        verdict = "yes: picking the style per civ wins clearly more; a strategy selector is worth building"
    elif per_civ / 2 < 20:
        verdict = (f"not shown: about {per_civ / 2:.0f} maps per civ to choose from is too few, so the choices are"
                   " mostly luck. More matches (--matches), or fewer civs in [match] civs, give each civ more")
    else:
        verdict = "no clear gain: one style for every civ does as well, so a per-civ selector has little to gain"
    lines.append(f"  Verdict: {verdict}.")

    names = list(results)
    lines.append("")
    lines.append("wins / matches per civ:")
    lines.append("  civ    " + "".join(f"{n:>10s}" for n in names))
    for g, row in analysis["by_group"].items():
        cells = "".join(f"{(f'{row[n][0]}/{row[n][1]}' if n in row else '-'):>10s}" for n in names)
        lines.append(f"  {g:7s}{cells}")
    if per_civ < 30:
        lines.append(f"  (about {per_civ:.0f} matches per civ: a single civ's numbers are mostly luck)")
    return lines


def _they(names: list[str], one: str, many: str) -> str:
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{joined} {one if len(names) == 1 else many}"

"""Run many matches in parallel for learning, evaluation and the finish test.

Each worker is one headless game at a time. A worker that plays a bot with
settings under test uses its own training AI ("slot" ``zadbot_t<n>``), so
parallel matches never share a params.js.
"""

from __future__ import annotations

import json
import math
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import BotConfig
from .game import GameInstall
from .learner import CrossEntropyLearner, Scenario, make_scenarios
from .match import RECORDER_AI, MatchResult, MatchSpec, PlayerSpec, outcome, run_match
from .modinstall import write_slot
from .params import unit_to_values

Log = Callable[[str], None]


class IncompatibleGame(RuntimeError):
    pass


@dataclass
class Job:
    scenario: Scenario | None = None
    values: dict[str, float] | None = None  # bot settings to try (uses a slot AI)
    bot_ai: str = ""  # or a fixed AI for the bot ("petra", "zadbot")
    spec: MatchSpec | None = None  # or a complete match (finish test)
    tag: dict = field(default_factory=dict)


class MatchRunner:
    """``worker_games``: an own (install, user data) per worker, from
    copies.prepare_copies; None = all workers share ``game``."""

    def __init__(self, cfg: BotConfig, game: GameInstall, user_data: Path, out_dir: Path | None, log: Log,
                 worker_games: list[tuple[GameInstall, Path]] | None = None):
        self.cfg = cfg
        self.game = game
        self.user_data = user_data
        self.out_dir = out_dir
        self.log = log
        self.worker_games = worker_games
        self._slots: queue.Queue[int] = queue.Queue()
        for i in range(cfg.game.workers):
            self._slots.put(i)
        self._lock = threading.Lock()
        self._done = 0

    def _spec(self, job: Job, slot: int | None, user_data: Path) -> MatchSpec:
        if job.spec is not None:
            return job.spec
        assert job.scenario is not None
        if job.values is not None:
            assert slot is not None
            ai = write_slot(user_data, slot, job.values, comment=json.dumps(job.tag))
        else:
            ai = job.bot_ai
        return job.scenario.spec(ai, self.cfg.match)

    def run_one(self, job: Job) -> MatchResult:
        slot = self._slots.get()
        try:
            game, user_data = self.worker_games[slot] if self.worker_games else (self.game, self.user_data)
            spec = self._spec(job, slot, user_data)
            result = run_match(game, spec, user_data, timeout=self.cfg.game.match_timeout,
                               startup_timeout=self.cfg.game.startup_timeout)
        finally:
            self._slots.put(slot)
        if result.status == "incompatible":
            raise IncompatibleGame("; ".join(result.errors))
        self._save(result, job.tag)
        with self._lock:
            self._done += 1
            n = self._done
        label = " ".join(f"{k}={v}" for k, v in job.tag.items())
        self.log(f"  [{n}] {label}: {result.summary()}")
        return result

    def run_all(self, jobs: list[Job]) -> list[MatchResult]:
        self._done = 0
        with ThreadPoolExecutor(max_workers=self.cfg.game.workers) as pool:
            return list(pool.map(self.run_one, jobs))

    def _save(self, result: MatchResult, tag: dict) -> None:
        if self.out_dir is None:
            return
        d = self.out_dir / "matches"
        d.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + "_" + "_".join(f"{k}{v}" for k, v in tag.items())
        path = d / f"{name}.json"
        i = 1
        while path.exists():
            path = d / f"{name}-{i}.json"
            i += 1
        path.write_text(json.dumps({"tag": tag, **result.to_dict()}, indent=1), encoding="utf-8")


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def score_margin(result: MatchResult, pid: int) -> float | None:
    if not result.players:
        return None
    me = result.player(pid)["score"]
    other = max(p["score"] for p in result.players if p["id"] != pid)
    return (me - other) / max(me + other, 1.0)


# ------------------------------------------------------------------ learning
def learn(
    cfg: BotConfig, runner: MatchRunner, learner: CrossEntropyLearner, generations: int,
    state_path: Path, log: Log,
) -> CrossEntropyLearner:
    for _ in range(generations):
        g = learner.generation
        scenarios = learner.scenarios()
        cands = learner.candidates()
        log(f"generation {g}: {len(cands)} candidates x {len(scenarios)} matches vs {cfg.match.opponent}")
        jobs = [
            Job(scenario=s, values=unit_to_values(c), tag={"gen": g, "cand": ci, "match": si})
            for ci, c in enumerate(cands) for si, s in enumerate(scenarios)
        ]
        results = runner.run_all(jobs)
        fitness, details = [], []
        for ci in range(len(cands)):
            rs = results[ci * len(scenarios):(ci + 1) * len(scenarios)]
            outs = [outcome(r, s.bot_side) for r, s in zip(rs, scenarios)]
            usable = [o for o in outs if o is not None]
            # a candidate without a single finished match counts as a loss
            fitness.append(_mean(usable) if usable else -1.0)
            details.append({
                "outcomes": outs,
                "matches": [
                    {"status": r.status, "reason": r.reason, "bot_won": s.bot_side in r.winners,
                     "game_time": round(r.game_time), "replay": r.replay_dir}
                    for r, s in zip(rs, scenarios)
                ],
            })
        learner.update(cands, fitness, details)
        learner.save(state_path)
        h = learner.history[-1]
        log(f"generation {g} done: centre {h['centre_fitness']:+.2f}, best {h['best_fitness']:+.2f} "
            f"(candidate {h['elite'][0]}); saved {state_path}")
    return learner


# ------------------------------------------------------------------ evaluation
def evaluate(
    cfg: BotConfig, runner: MatchRunner, variants: dict[str, dict[str, float] | str], n: int, seed: int,
) -> dict[str, dict]:
    """Each variant (settings, or a fixed AI name) plays the same ``n`` fresh
    scenarios against the opponent. Returns win/loss statistics per variant."""
    scenarios = make_scenarios(cfg.match, n, seed)
    out, outcomes = {}, {}
    for name, v in variants.items():
        jobs = [
            Job(scenario=s, values=v if isinstance(v, dict) else None, bot_ai=v if isinstance(v, str) else "",
                tag={"eval": name, "match": i})
            for i, s in enumerate(scenarios)
        ]
        results = runner.run_all(jobs)
        out[name] = summarize(results, [s.bot_side for s in scenarios])
        outcomes[name] = [outcome(r, s.bot_side) for r, s in zip(results, scenarios)]
        out[name]["outcomes"] = outcomes[name]
    if "petra" in outcomes:
        for name in outcomes:
            if name != "petra":
                out[name]["paired"] = paired_comparison(outcomes[name], outcomes["petra"])
    return out


def paired_comparison(a: list[float | None], b: list[float | None]) -> dict:
    """Two variants on the same scenarios: on how many did only one of them
    win, and how likely is a split at least that uneven by pure chance (exact
    two-sided sign test, i.e. McNemar)."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    better = sum(1 for x, y in pairs if x > 0 >= y)
    worse = sum(1 for x, y in pairs if y > 0 >= x)
    n = better + worse
    total = len(pairs)
    if n == 0:
        return {"better": 0, "worse": 0, "p": 1.0, "matches": total, "diff": 0.0, "range": 0.0}
    k = max(better, worse)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    # difference in win rate over all paired matches, with a 95 % range
    diff = (better - worse) / total
    var = max(0.0, n - (better - worse) ** 2 / total) / total ** 2
    return {"better": better, "worse": worse, "p": min(1.0, 2 * tail), "matches": total,
            "diff": diff, "range": 1.96 * math.sqrt(var)}


def _best(outcomes: dict[str, list[float | None]], idx: list[int], default: str | None) -> str | None:
    """The variant with the highest mean outcome on matches ``idx``; ``default`` on a tie or without data."""
    means = {}
    for name, outs in outcomes.items():
        xs = [outs[i] for i in idx if outs[i] is not None]
        if xs:
            means[name] = sum(xs) / len(xs)
    if not means:
        return default
    top = max(means.values())
    if default in means and means[default] >= top - 1e-9:
        return default
    return max(means, key=means.get)


def style_analysis(outcomes: dict[str, list[float | None]], groups: list[str]) -> dict:
    """Would it help to pick a variant per group (civilisation)?

    Honest estimate: the matches are split in two halves. On one half the
    best variant overall and the best per group are chosen, on the other half
    they are played; then the other way round. So every match is scored by
    choices made without it, like a selector meeting new maps. Returns win
    counts per group and variant, and the chosen-per-group vs one-for-all
    comparison (see paired_comparison)."""
    n = len(groups)
    half = [(i // 2) % 2 for i in range(n)]  # the bot alternates sides: both halves get both sides
    per_group = [None] * n
    one_for_all = [None] * n
    choices = []
    for h in (0, 1):
        train = [i for i in range(n) if half[i] != h]
        overall = _best(outcomes, train, None)
        if overall is None:
            continue
        picks = {}
        for g in sorted({groups[i] for i in range(n) if half[i] == h}):
            picks[g] = _best(outcomes, [i for i in train if groups[i] == g], overall)
        choices.append({"overall": overall, "per_group": picks})
        for i in range(n):
            if half[i] == h:
                per_group[i] = outcomes[picks[groups[i]]][i]
                one_for_all[i] = outcomes[overall][i]
    table: dict[str, dict[str, list[int]]] = {}
    for name, outs in outcomes.items():
        for g, o in zip(groups, outs):
            if o is None:
                continue
            cell = table.setdefault(g, {}).setdefault(name, [0, 0])
            cell[0] += o > 0
            cell[1] += 1

    def win_rate(xs):
        done = [x for x in xs if x is not None]
        return sum(x > 0 for x in done) / len(done) if done else float("nan")

    counts = [sum(g == x for x in groups) for g in sorted(set(groups))]
    return {
        "by_group": {g: table[g] for g in sorted(table)},
        "choices": choices,
        "per_group_win_rate": win_rate(per_group),
        "one_for_all_win_rate": win_rate(one_for_all),
        "selector": paired_comparison(per_group, one_for_all),
        "matches_per_group": sum(counts) / len(counts) if counts else 0.0,
    }


def summarize(results: list[MatchResult], bot_ids: list[int]) -> dict:
    outs, margins, wins, losses, draws, conquest_wins, conquest_losses, unfinished = [], [], 0, 0, 0, 0, 0, 0
    times = []
    for r, pid in zip(results, bot_ids):
        o = outcome(r, pid)
        if o is None:
            unfinished += 1
            continue
        outs.append(o)
        m = score_margin(r, pid)
        if m is not None:
            margins.append(m)
        times.append(r.game_time)
        if o > 0:
            wins += 1
            conquest_wins += r.reason == "conquest"
        elif o < 0:
            losses += 1
            conquest_losses += r.reason == "conquest"
        else:
            draws += 1
    played = wins + losses + draws
    return {
        "matches": len(results), "finished": played, "unfinished": unfinished,
        "wins": wins, "losses": losses, "draws": draws,
        "conquest_wins": conquest_wins, "conquest_losses": conquest_losses,
        "win_rate": wins / played if played else float("nan"),
        "mean_outcome": _mean(outs), "mean_score_margin": _mean(margins),
        "mean_game_minutes": _mean(times) / 60 if times else float("nan"),
    }


# ------------------------------------------------------------------ finish test
def finish_test(
    runner: MatchRunner, bot: PlayerSpec, opponent: PlayerSpec, games: int, map_name: str, size: int, seed: int,
) -> list[MatchResult]:
    """Full games without a time limit: does the bot finish the game (conquest)?"""
    jobs = []
    for i in range(games):
        players = [PlayerSpec(**vars(bot)), PlayerSpec(**vars(opponent))]
        spec = MatchSpec(players=players, map=map_name, size=size, seed=seed + i, ai_seed=seed + i, time_limit=0)
        jobs.append(Job(spec=spec, tag={"finish": i}))
    return runner.run_all(jobs)


# ------------------------------------------------------------------ recording (teacher data)
def record_specs(
    cfg: BotConfig, games: int, seed: int, difficulties: list[int], behaviors: list[str], sizes: list[int],
    time_limit: int,
) -> list[MatchSpec]:
    """Petra vs Petra, both recording (transfer/DESIGN.md): varied difficulty,
    behaviour, civilisations, maps, map sizes and seeds, so the teacher data
    covers many situations rather than one opening played many times."""
    rng = random.Random(seed)
    specs = []
    for i in range(games):
        players = [PlayerSpec(ai=RECORDER_AI, difficulty=rng.choice(difficulties), behavior=rng.choice(behaviors),
                              civ=rng.choice(cfg.match.civs)) for _ in range(2)]
        specs.append(MatchSpec(players=players, map=cfg.match.maps[i % len(cfg.match.maps)], size=rng.choice(sizes),
                               seed=rng.randrange(1, 1_000_000), ai_seed=rng.randrange(1, 1_000_000),
                               time_limit=time_limit))
    return specs


def record(runner: MatchRunner, specs: list[MatchSpec], out_dir: Path, seed: int, log: Log) -> dict:
    """Play ``specs`` and save each game's recording as ``<out_dir>/<seed>-<i>.jsonl``:
    one line about the game (players, result), then the recorder's lines.
    Games whose file exists are skipped, so an interrupted run resumes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [(i, s) for i, s in enumerate(specs) if not (out_dir / f"{seed}-{i}.jsonl").is_file()]
    if len(todo) < len(specs):
        log(f"{len(specs) - len(todo)} of {len(specs)} games already recorded in {out_dir}; playing the rest")
    jobs = [Job(spec=s, tag={"record": f"{seed}-{i}"}) for i, s in todo]
    summary = {"games": 0, "unfinished": 0, "steps": 0, "no_recording": 0, "errors": [], "actions": {},
               "done_before": len(specs) - len(todo)}
    for (i, spec), result in zip(todo, runner.run_all(jobs)):
        errors = [r for r in result.recording if "error" in r]
        steps = [r for r in result.recording if "t" in r]
        summary["errors"] += [f"{seed}-{i} P{r.get('p')}: {r['error']}" for r in errors]
        if not steps:
            summary["no_recording"] += 1
            continue
        game = {
            "spec": spec.to_dict(), "status": result.status, "reason": result.reason, "winners": result.winners,
            "game_time": result.game_time, "players": result.players, "replay": result.replay_dir,
        }
        path = out_dir / f"{seed}-{i}.jsonl"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"game": game}) + "\n")
            for r in result.recording:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        tmp.replace(path)
        summary["games"] += 1
        summary["unfinished"] += not result.finished
        summary["steps"] += len(steps)
        for r in steps:
            for name, n in r.get("a", {}).items():
                summary["actions"][name] = summary["actions"].get(name, 0) + n
    return summary

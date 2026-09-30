"""Learning Petra's settings by playing against Petra (cross-entropy search).

The bot is Petra with ~10 of its settings changed (params.py). Each
generation:

1. Pick scenarios: map, seed, civilisation, and which side the bot plays.
   Every candidate plays the *same* scenarios, and a match with the same
   seeds and settings plays out the same way, so candidates are compared on
   equal terms.
2. Candidates: the current centre (the bot as learned so far) and
   ``population - 1`` variations around it (normal noise with the current
   spread, in 0..1 units of each setting's range).
3. Each candidate plays its scenarios against plain Petra. Fitness is the
   mean outcome: +1 conquest win, -1 conquest loss, and when the time limit
   decides, the score margin within +-0.5.
4. The centre moves towards the ``elite`` best candidates, and the spread
   shrinks to how much those differ (never below ``min_std``).

The centre is the learned bot: ``zadbot install-mod`` puts it into the game
and ``zadbot evaluate`` measures it on seeds it never trained on. The
centre's own fitness per generation is the learning curve.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from . import GAME_VERSION
from .config import LearnConfig, MatchConfig
from .match import MatchSpec, PlayerSpec
from .params import BY_NAME, PARAMS, defaults, unit_to_values, values_to_unit


@dataclass
class Scenario:
    map: str
    size: int
    seed: int
    ai_seed: int
    bot_civ: str
    opponent_civ: str
    bot_side: int  # 1 or 2

    def spec(self, bot_ai: str, m: MatchConfig) -> MatchSpec:
        bot = PlayerSpec(ai=bot_ai, difficulty=m.difficulty, behavior="balanced", civ=self.bot_civ)
        opp = PlayerSpec(ai=m.opponent, difficulty=m.difficulty, behavior=m.opponent_behavior, civ=self.opponent_civ)
        players = [bot, opp] if self.bot_side == 1 else [opp, bot]
        return MatchSpec(players=players, map=self.map, size=self.size, seed=self.seed,
                         ai_seed=self.ai_seed, time_limit=m.time_limit)


def make_scenarios(m: MatchConfig, n: int, seed: int) -> list[Scenario]:
    """``n`` scenarios; the bot alternates sides so neither start position is favoured."""
    rng = random.Random(seed)
    out = []
    for k in range(n):
        civ = rng.choice(m.civs)
        opp_civ = civ if m.mirror else rng.choice(m.civs)
        out.append(Scenario(
            map=m.maps[k % len(m.maps)], size=m.size, seed=rng.randrange(1, 1_000_000),
            ai_seed=rng.randrange(1, 1_000_000), bot_civ=civ, opponent_civ=opp_civ, bot_side=1 + k % 2,
        ))
    return out


class CrossEntropyLearner:
    FORMAT = 1

    def __init__(self, cfg: LearnConfig, match: MatchConfig):
        self.cfg = cfg
        self.match = match
        self.mean = values_to_unit(defaults())
        self.std = {p.name: cfg.init_std for p in PARAMS}
        self.generation = 0
        self.history: list[dict] = []

    # ---------------------------------------------------------- one generation
    def scenarios(self, generation: int | None = None) -> list[Scenario]:
        g = self.generation if generation is None else generation
        return make_scenarios(self.match, self.cfg.matches_per_candidate, self.cfg.seed + 7919 * g)

    def candidates(self) -> list[dict[str, float]]:
        """Settings (unit space) to try this generation; the first is the centre."""
        rng = random.Random(self.cfg.seed * 31 + self.generation)
        out = [dict(self.mean)]
        for _ in range(self.cfg.population - 1):
            out.append({k: min(1.0, max(0.0, rng.gauss(self.mean[k], self.std[k]))) for k in self.mean})
        return out

    def update(self, candidates: list[dict[str, float]], fitness: list[float], details: list[dict] | None = None) -> None:
        """Move the centre towards the best candidates and record the generation."""
        if len(candidates) != len(fitness):
            raise ValueError("one fitness per candidate")
        order = sorted(range(len(candidates)), key=lambda i: -fitness[i])
        elite = [candidates[i] for i in order[: self.cfg.elite]]
        a = self.cfg.smoothing
        old_mean, old_std = dict(self.mean), dict(self.std)
        for k in self.mean:
            vals = [e[k] for e in elite]
            mu = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
            self.mean[k] = (1 - a) * self.mean[k] + a * mu
            self.std[k] = max(self.cfg.min_std, (1 - a) * self.std[k] + a * sd)
        self.history.append({
            "generation": self.generation,
            "centre_before": unit_to_values(old_mean),
            "spread_before": old_std,
            "candidates": [
                {"values": unit_to_values(c), "fitness": f, **((details or [{}] * len(candidates))[i])}
                for i, (c, f) in enumerate(zip(candidates, fitness))
            ],
            "elite": order[: self.cfg.elite],
            "centre_fitness": fitness[0],
            "best_fitness": fitness[order[0]],
            "centre_after": unit_to_values(self.mean),
        })
        self.generation += 1

    # ---------------------------------------------------------- results
    def centre_values(self) -> dict[str, float]:
        return unit_to_values(self.mean)

    def curve(self) -> list[tuple[int, float, float]]:
        """(generation, centre fitness, best fitness) per generation."""
        return [(h["generation"], h["centre_fitness"], h["best_fitness"]) for h in self.history]

    # ---------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "format": self.FORMAT,
            "game_version": GAME_VERSION,
            "learn": asdict(self.cfg),
            "match": asdict(self.match),
            "generation": self.generation,
            "mean": self.mean,
            "std": self.std,
            "centre": self.centre_values(),
            "history": self.history,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path, cfg: LearnConfig, match: MatchConfig) -> "CrossEntropyLearner":
        """Resume. The match setup must be the one the state was learned on."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != cls.FORMAT:
            raise ValueError(f"{path}: unsupported learner format {data.get('format')}")
        if data.get("game_version") != GAME_VERSION:
            raise ValueError(f"{path} was learned on 0 A.D. {data.get('game_version')}, this bot is for {GAME_VERSION}")
        if data.get("match") != asdict(match):
            raise ValueError(f"{path} was learned with different [match] settings; use a new --state file")
        obj = cls(cfg, match)
        obj.generation = data["generation"]
        obj.history = data["history"]
        # settings added later start at their default
        obj.mean.update({k: v for k, v in data["mean"].items() if k in obj.mean})
        obj.std.update({k: v for k, v in data["std"].items() if k in obj.std})
        return obj


def load_centre(path: str | Path) -> dict[str, float]:
    """The learned settings from a learner state file (settings this version
    no longer has are dropped)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in data["centre"].items() if k in BY_NAME}

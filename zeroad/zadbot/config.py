"""Configuration: one TOML file says where the game is and which matches the
bot learns from. Every section maps onto a dataclass below; unknown keys are
rejected so typos do not silently fall back to defaults."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# Playable civilisations of 0 A.D. 0.28.0 (simulation/data/civs/).
CIVS_0_28 = [
    "athen", "brit", "cart", "gaul", "germ", "han", "iber", "kush",
    "mace", "maur", "pers", "ptol", "rome", "sele", "spart",
]


@dataclass
class GameConfig:
    # pyrogenesis(.exe), or the install folder. "" = search the usual places.
    # "fake" = the built-in stand-in (no game needed, for trying the pipeline).
    path: str = ""
    # Where 0 A.D. keeps mods and replays. "" = the game's default for this OS.
    user_data: str = ""
    workers: int = 2  # matches run in parallel (each is one headless game); always 1 on Windows
    match_timeout: float = 3 * 3600.0  # s of real time before a match is stopped
    startup_timeout: float = 300.0  # s for a game to start its match before it counts as stuck
    # Windows, workers > 1: one copy of the game per worker (~3.7 GB each), see copies.py
    copies_dir: str = "runs/game-copies"


@dataclass
class MatchConfig:
    """The matches the bot learns from and is tested on (1 v 1)."""

    maps: list[str] = field(default_factory=lambda: ["random/mainland"])
    size: int = 128  # random map size in tiles (128 tiny, 192 small, 256 medium)
    difficulty: int = 3  # both players: 0 sandbox .. 5 very hard (3 = no AI bonus)
    opponent: str = "petra"
    opponent_behavior: str = "balanced"  # random | balanced | aggressive | defensive
    civs: list[str] = field(default_factory=lambda: list(CIVS_0_28))
    mirror: bool = True  # both players get the same civ (fairer, less noise)
    # Minutes until the match is decided by score. 0 = play until conquest.
    time_limit: int = 30


@dataclass
class LearnConfig:
    """Cross-entropy search over Petra's settings (see learner.py)."""

    population: int = 8  # settings tried per generation (the first is the current centre)
    matches_per_candidate: int = 2  # each on the same maps/seeds for all candidates
    elite: int = 3  # best candidates the centre moves towards
    smoothing: float = 0.7  # how far the centre moves towards them (0..1)
    init_std: float = 0.25  # starting spread, in 0..1 units of each setting's range
    min_std: float = 0.04
    seed: int = 1000  # training seeds start here; evaluation uses its own


@dataclass
class EvaluateConfig:
    matches: int = 20
    seed: int = 1  # evaluation seeds start here, away from the training seeds


@dataclass
class BotConfig:
    game: GameConfig = field(default_factory=GameConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    learn: LearnConfig = field(default_factory=LearnConfig)
    evaluate: EvaluateConfig = field(default_factory=EvaluateConfig)


_SECTIONS = {
    "game": GameConfig,
    "match": MatchConfig,
    "learn": LearnConfig,
    "evaluate": EvaluateConfig,
}


def _build(cls: type, values: dict[str, Any], where: str) -> Any:
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(values) - names
    if unknown:
        raise ValueError(f"unknown key(s) in [{where}]: {', '.join(sorted(unknown))}")
    return cls(**values)


def config_from_dict(data: dict[str, Any]) -> BotConfig:
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
    cfg = BotConfig(**{name: _build(cls, data.get(name, {}), name) for name, cls in _SECTIONS.items()})
    validate(cfg)
    return cfg


def validate(cfg: BotConfig) -> None:
    m, lc = cfg.match, cfg.learn
    if not 0 <= m.difficulty <= 5:
        raise ValueError("[match] difficulty must be 0..5")
    if not m.maps:
        raise ValueError("[match] maps must not be empty")
    if not m.civs:
        raise ValueError("[match] civs must not be empty")
    if m.time_limit < 0:
        raise ValueError("[match] time_limit must be >= 0 (0 = play until conquest)")
    if not 1 <= lc.elite < lc.population:
        raise ValueError("[learn] elite must be at least 1 and below population")
    if lc.matches_per_candidate < 1:
        raise ValueError("[learn] matches_per_candidate must be >= 1")
    if cfg.game.workers < 1:
        raise ValueError("[game] workers must be >= 1")


def load_config(path: str | Path) -> BotConfig:
    with open(path, "rb") as fh:
        return config_from_dict(tomllib.load(fh))

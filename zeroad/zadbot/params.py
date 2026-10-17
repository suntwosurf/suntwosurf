"""The settings the bot learns: a few numbers inside Petra's Config.

Each setting is one "concept" of how to play (when to advance, how many
workers, how eager to attack...). Values are either set directly ("set") or
multiply what Petra itself chose for this game ("scale"), so the default
values below are plain Petra and every learned change is relative to it.

The learner works in 0..1 units per setting (``to_unit`` / ``from_unit``);
the game gets real values through the generated ``params.js``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    name: str
    target: str  # path in Petra's Config (simulation/ai/petra/config.js)
    mode: str  # "set" | "scale"
    lo: float
    hi: float
    default: float
    log: bool = False  # search evenly in log space (for multipliers)
    round: bool = False
    min_value: float | None = None
    help: str = ""


PARAMS: list[Param] = [
    Param("aggressive", "personality.aggressive", "set", 0.0, 1.0, 0.5,
          help="eagerness to attack: above 0.7 Petra plans early rushes, below 0.3 none"),
    Param("defensive", "personality.defensive", "set", 0.0, 1.0, 0.5,
          help="towers, fortresses, bigger defence and garrisons"),
    Param("phase2_pop", "Economy.popPhase2", "scale", 0.3, 1.5, 1.0, log=True, round=True, min_value=10,
          help="population before advancing to the town phase"),
    Param("phase3_workers", "Economy.workPhase3", "scale", 0.5, 1.5, 1.0, log=True, round=True, min_value=20,
          help="workers before advancing to the city phase"),
    Param("workers", "Economy.targetNumWorkers", "scale", 0.5, 1.5, 1.0, log=True, round=True, min_value=10,
          help="target number of workers"),
    Param("support_ratio", "Economy.supportRatio", "scale", 0.5, 2.0, 1.0, log=True,
          help="share of support units (women) among the workers"),
    Param("barracks1_pop", "Military.popForBarracks1", "scale", 0.5, 2.0, 1.0, log=True, round=True, min_value=5,
          help="population when the first barracks is built"),
    Param("barracks2_pop", "Military.popForBarracks2", "scale", 0.5, 2.0, 1.0, log=True, round=True, min_value=10,
          help="population when the second barracks is built"),
    Param("soldier_priority", "priorities.citizenSoldier", "scale", 0.5, 2.0, 1.0, log=True, round=True, min_value=1,
          help="share of resources for training citizen soldiers"),
    Param("military_building_priority", "priorities.militaryBuilding", "scale", 0.5, 2.0, 1.0, log=True, round=True,
          min_value=1, help="share of resources for military buildings"),
]

BY_NAME = {p.name: p for p in PARAMS}


def defaults() -> dict[str, float]:
    return {p.name: p.default for p in PARAMS}


def clamp(values: dict[str, float]) -> dict[str, float]:
    """Complete ``values`` with defaults and keep each inside its range."""
    unknown = set(values) - set(BY_NAME)
    if unknown:
        raise KeyError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    out = defaults()
    for name, v in values.items():
        p = BY_NAME[name]
        out[name] = min(p.hi, max(p.lo, float(v)))
    return out


def to_unit(p: Param, value: float) -> float:
    if p.log:
        u = (math.log(value) - math.log(p.lo)) / (math.log(p.hi) - math.log(p.lo))
    else:
        u = (value - p.lo) / (p.hi - p.lo)
    return min(1.0, max(0.0, u))


def from_unit(p: Param, u: float) -> float:
    u = min(1.0, max(0.0, u))
    if p.log:
        return math.exp(math.log(p.lo) + u * (math.log(p.hi) - math.log(p.lo)))
    return p.lo + u * (p.hi - p.lo)


def values_to_unit(values: dict[str, float]) -> dict[str, float]:
    return {p.name: to_unit(p, v) for p, v in ((BY_NAME[k], v) for k, v in clamp(values).items())}


def unit_to_values(unit: dict[str, float]) -> dict[str, float]:
    return {name: from_unit(BY_NAME[name], u) for name, u in unit.items()}


def learned_entries(values: dict[str, float]) -> dict[str, dict]:
    """What simulation/ai/zadbot/learned.js applies, keyed by Config path."""
    out = {}
    for name, v in clamp(values).items():
        p = BY_NAME[name]
        entry: dict = {"mode": p.mode, "value": round(v, 4)}
        if p.round:
            entry["round"] = True
        if p.min_value is not None:
            entry["min"] = p.min_value
        out[p.target] = entry
    return out


def params_js(values: dict[str, float] | None, comment: str = "") -> str:
    """The params.js module for one zadbot AI. ``None`` = plain Petra."""
    body = json.dumps(learned_entries(values), indent="\t", sort_keys=True) if values is not None else "{}"
    lines = ["// Written by zadbot. Empty means plain Petra."]
    if comment:
        lines.append("// " + comment)
    lines.append(f"export const LEARNED = {body};")
    return "\n".join(lines) + "\n"


def parse_params_js(text: str) -> dict[str, dict]:
    """Inverse of ``params_js`` (used by the fake game and the tests)."""
    start = text.index("export const LEARNED =") + len("export const LEARNED =")
    end = text.rindex(";")
    return json.loads(text[start:end])


def describe(values: dict[str, float]) -> list[str]:
    """One line per setting, in words, compared with plain Petra."""
    lines = []
    for name, v in clamp(values).items():
        p = BY_NAME[name]
        if p.mode == "scale":
            change = "same as Petra" if abs(v - 1) < 0.02 else f"{v:.2f} x Petra"
        else:
            change = f"{v:.2f} (Petra {p.default:.2f})"
        lines.append(f"{name:28s} {change:22s} {p.help}")
    return lines

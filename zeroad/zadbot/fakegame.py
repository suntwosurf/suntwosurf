"""A stand-in for pyrogenesis, for trying and testing zadbot without the game.

It takes the same command line as the real headless game, checks the mod's
version dependency the same way, reads the zadbot AI's params.js, "plays"
a pretend match in a fraction of a second, and writes the same replay files
(commands.txt, metadata.json) and stdout lines as the real game.

The pretend match is NOT 0 A.D.: a player's strength is made up from a
hidden optimum of the zadbot settings, so the learner has something to find.
Petra's default settings have strength 0. Nothing learned here says
anything about the real game.
"""

from __future__ import annotations

import html
import json
import math
import os
import random
import sys
import time
from pathlib import Path

from .game import default_user_data
from .params import BY_NAME, parse_params_js, to_unit

# the made-up optimum the fake game rewards (0..1 units of each setting)
HIDDEN_OPTIMUM = {"aggressive": 0.8, "defensive": 0.3, "phase2_pop": 0.35, "workers": 0.75}
HIDDEN_WEIGHT = 3.0


def default_data_dir() -> Path:
    return Path(__file__).parent / "fakedata"


def _args(argv: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in argv:
        key, _, value = a.lstrip("-").partition("=")
        out.setdefault(key, []).append(value)
    return out


def _version_ok(have: str, dep: str) -> bool:
    """Same rule as Mod::CompareVersionStrings for the operators we use."""
    for op in ("<=", ">=", "=", "<", ">"):
        if op in dep:
            name, want = dep.split(op, 1)
            h = [int(x) for x in have.split(".")]
            w = [int(x) for x in want.split(".")]
            if op == "=":
                return h == w
            if op in ("<=", "<"):
                return h < w or (op == "<=" and h == w)
            return h > w or (op == ">=" and h == w)
    return True


def strength(user_data: Path, ai: str, log: "_Log | None" = None) -> float:
    if ai == "petra":
        return 0.0
    params = user_data / "mods" / "zadbot" / "simulation" / "ai" / ai / "params.js"
    if not params.is_file():
        raise FileNotFoundError(f"Failed to create AI player: simulation/ai/{ai}/data.json not found")
    learned = parse_params_js(params.read_text(encoding="utf-8"))
    by_target = {p.target: p for p in BY_NAME.values()}
    s = 0.0
    for name, opt in HIDDEN_OPTIMUM.items():
        p = BY_NAME[name]
        default_u = to_unit(p, p.default)
        entry = learned.get(p.target)
        u = to_unit(p, entry["value"]) if entry else default_u
        s += HIDDEN_WEIGHT * ((default_u - opt) ** 2 - (u - opt) ** 2)
    unknown = [t for t in learned if t not in by_target]
    if unknown and log is not None:
        log.warning("PlayerID 1 |   zadbot: unknown Petra settings: " + ", ".join(unknown))
    return s


def _new_replay_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    i = 1
    while True:
        d = root / f"{day}_{i:04d}"
        try:
            d.mkdir()
            return d
        except FileExistsError:
            i += 1


def _sequences(times: list[float], final_score: float, kills: float, explored: float) -> dict:
    n = len(times)
    frac = [t / times[-1] if times[-1] else 1.0 for t in times]
    econ = [f * final_score * 10 * 0.8 for f in frac]  # economy score * 10 = resources
    return {
        "time": times,
        "resourcesGathered": {
            "food": [e * 0.4 for e in econ], "wood": [e * 0.4 for e in econ],
            "stone": [e * 0.1 for e in econ], "metal": [e * 0.1 for e in econ],
            "vegetarianFood": [e * 0.2 for e in econ],
        },
        "tradeIncome": [0] * n,
        "enemyUnitsKilledValue": [f * kills for f in frac],
        "unitsCapturedValue": [0] * n,
        "enemyBuildingsDestroyedValue": [0] * n,
        "buildingsCapturedValue": [0] * n,
        "percentMapExplored": [round(f * explored) for f in frac],
        "unitsTrained": {"total": [round(f * 80) for f in frac]},
        "unitsLost": {"total": [round(f * 40) for f in frac]},
        "enemyUnitsKilled": {"total": [round(f * 40) for f in frac]},
        "buildingsLost": {"total": [0] * n},
        "enemyBuildingsDestroyed": {"total": [0] * n},
    }


class _Log:
    """Like the engine: errors go to stdout (not on Windows, see
    ZADBOT_FAKE_WINDOWS) and, with -unique-logs, to
    <logs>/interestinglog_<time>_<pid>.html."""

    def __init__(self, logs_dir: Path | None, windows: bool):
        self.windows = windows
        self.file = None
        if logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            path = logs_dir / f"interestinglog_{int(time.time())}_{os.getpid()}.html"
            self.file = open(path, "w", encoding="utf-8")
            self.file.write("<html><body><h1>Pyrogenesis Log</h1>\n")

    def print(self, text: str) -> None:
        if not self.windows:
            print(text, flush=True)

    def error(self, msg: str) -> None:
        self.print(f"ERROR: {msg}")
        if self.file:
            self.file.write(f'<p class="error">ERROR: {html.escape(msg)}</p>\n')

    def warning(self, msg: str) -> None:
        self.print(f"WARNING: {msg}")
        if self.file:
            self.file.write(f'<p class="warning">WARNING: {html.escape(msg)}</p>\n')

    def close(self) -> None:
        if self.file:
            self.file.write("<p>Engine exited successfully</p>\n")
            self.file.close()


def main(argv: list[str] | None = None) -> int:
    a = _args(sys.argv[1:] if argv is None else argv)
    user_data = Path(os.environ.get("ZADBOT_USER_DATA") or default_user_data())
    log = _Log(user_data / "logs" if "unique-logs" in a else None, bool(os.environ.get("ZADBOT_FAKE_WINDOWS")))
    try:
        return _play(a, user_data, log)
    finally:
        log.close()


def _play(a: dict[str, list[str]], user_data: Path, log: _Log) -> int:
    data_dir = Path(a.get("data-dir", [str(default_data_dir())])[0])
    version = json.loads((data_dir / "mods" / "public" / "mod.json").read_text(encoding="utf-8"))["version"]

    for mod in a.get("mod", []):
        if mod in ("public", "mod"):
            continue
        mod_json = user_data / "mods" / mod / "mod.json"
        deps = json.loads(mod_json.read_text(encoding="utf-8")).get("dependencies", []) if mod_json.is_file() else None
        if deps is None or not all(_version_ok(version, d) for d in deps):
            log.error(f"Trying to start with incompatible mods: {mod}.")
            return 0  # the real game also exits "successfully" here

    if "autostart-nonvisual" not in a or "autostart" not in a:
        log.error("the fake game only does -autostart-nonvisual matches")
        return 1

    fail = os.environ.get("ZADBOT_FAKE_FAIL")
    if fail:  # for tests: quit during loading, like a broken setup
        log.error(fail)
        return 0

    seed = int(a.get("autostart-seed", ["0"])[0])
    ai_seed = int(a.get("autostart-aiseed", ["0"])[0])
    n_players = int(a.get("autostart-players", ["2"])[0])

    def per_player(key: str) -> dict[int, str]:
        return {int(v.split(":", 1)[0]): v.split(":", 1)[1] for v in a.get(key, [])}

    ais, diffs, civs = per_player("autostart-ai"), per_player("autostart-aidiff"), per_player("autostart-civ")
    rng = random.Random(seed * 1_000_003 + ai_seed)
    player_civs = {i: (civs.get(i, "random") if civs.get(i, "random") != "random" else rng.choice(["athen", "rome", "gaul"]))
                   for i in range(1, n_players + 1)}

    limit = 0
    victory = a.get("autostart-victory", [])
    for v in victory:
        vc = user_data / "mods" / "zadbot" / "simulation" / "data" / "settings" / "victory_conditions" / f"{v}.json"
        if v.startswith("zadbot_limit_") and vc.is_file():  # like the game: unknown conditions are dropped
            limit = int(v.rsplit("_", 1)[1])

    try:
        strengths = {i: strength(user_data, ais.get(i, "petra"), log) + 0.4 * (int(diffs.get(i, "3")) - 3)
                     for i in range(1, n_players + 1)}
    except FileNotFoundError as e:
        log.error(str(e))
        return 1

    # the pretend match (2 players)
    adv = strengths[1] - strengths.get(2, 0.0) + rng.gauss(0, 0.3)
    stronger = 1 if adv >= 0 else 2
    if abs(adv) > 0.5:
        end_min, reason = 20 + 10 / abs(adv), "conquest"
        if limit and end_min > limit:
            end_min, reason = limit, "time_limit"
    elif limit:
        end_min, reason = limit, "time_limit"
    else:
        end_min, reason = 60 + 30 * (0.5 - abs(adv)), "conquest"
    end_s = round(end_min * 60)

    replay = _new_replay_dir(user_data / "replays" / version)
    attribs = {
        "map": "maps/" + a["autostart"][0], "mapType": a["autostart"][0].split("/")[0],
        "settings": {
            "Seed": seed, "AISeed": ai_seed, "VictoryConditions": victory,
            "PlayerData": [{"AI": ais.get(i, ""), "AIDiff": int(diffs.get(i, "3")), "Civ": player_civs[i]}
                           for i in range(1, n_players + 1)],
        },
    }
    (replay / "commands.txt").write_text("start " + json.dumps(attribs) + "\n", encoding="utf-8")
    log.print(f"FILES| Replay written to '{replay}'")

    base = 1500 * end_min / 30
    scores = {i: base * (1 + 0.15 * math.tanh(strengths[i] - strengths[3 - i] if n_players == 2 else 0))
              for i in range(1, n_players + 1)}
    times = [float(t) for t in range(0, end_s, 30)] + [float(end_s)]
    for t in range(60, end_s + 1, 60):
        log.print(f"Turn {t * 5} (200)...")
    states = {i: ("won" if i == stronger else "defeated") for i in range(1, n_players + 1)}
    player_states = [{"name": "Gaia", "civ": "gaia", "state": "active", "popCount": 0}]
    for i in range(1, n_players + 1):
        player_states.append({
            "name": f"Player {i}", "civ": player_civs[i], "state": states[i], "popCount": 100, "phase": "town",
            "sequences": _sequences(times, scores[i], kills=3000 if i == stronger else 1000, explored=40),
        })
    end_players = [{"id": i, "ai": ais.get(i, ""), "state": states[i], "score": round(scores[i])}
                   for i in range(1, n_players + 1)]
    log.print("ZADBOT " + json.dumps({"event": "end", "time": end_s, "reason": reason, "winners": [stronger],
                                      "players": end_players}))
    meta = {"timeElapsed": end_s * 1000, "playerStates": player_states, "mapSettings": attribs["settings"]}
    (replay / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    log.print(f"FILES| Replay metadata written to '{replay / 'metadata.json'}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

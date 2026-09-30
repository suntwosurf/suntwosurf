"""Run one headless 0 A.D. match and read its result.

The game runs with ``-autostart-nonvisual``: no window, as fast as the CPU
allows, and it exits by itself when a player has won (source/main.cpp).

Results come from the replay the game writes (``replays/<version>/<date>_<n>/``):
``metadata.json`` has every player's final state (won/defeated) and a stats
timeline, ``commands.txt`` starts with the match settings. That works on
every OS. On Linux/macOS the game also prints progress to stdout (the
"ZADBOT {...}" lines of ZadbotReport.js and "Turn N" lines); on Windows the
game sends that text to the debugger instead, so it is only a bonus.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .game import GameInstall
from .modinstall import MOD_NAME, ensure_time_limit

RECORDER_AI = "zadbot_rec"  # plain Petra that records (mod/zadbot/simulation/ai/zadbot_rec)

TURN_SECONDS = 0.2  # DEFAULT_TURN_LENGTH in the engine
POLL_SECONDS = 1.0

_TURN_RE = re.compile(r"^Turn (\d+) \(")
_REPLAY_RE = re.compile(r"Replay written to '(.+)'")
_CLAIM_LOCK = threading.Lock()
_CLAIMED: set[str] = set()


@dataclass
class PlayerSpec:
    ai: str = "petra"
    difficulty: int = 3
    behavior: str = "balanced"
    civ: str = ""  # "" = random
    team: int = 0  # 0 = no team

    @classmethod
    def parse(cls, text: str) -> "PlayerSpec":
        """"petra", "petra:5", "zadbot:3:aggressive"."""
        parts = text.split(":")
        spec = cls(ai=parts[0])
        if len(parts) > 1 and parts[1]:
            spec.difficulty = int(parts[1])
        if len(parts) > 2 and parts[2]:
            spec.behavior = parts[2]
        return spec

    def label(self) -> str:
        return f"{self.ai}:{self.difficulty}" + (f" ({self.civ})" if self.civ else "")


@dataclass
class MatchSpec:
    players: list[PlayerSpec]
    map: str = "random/mainland"
    size: int = 128
    seed: int = 0
    ai_seed: int = 0
    time_limit: int = 30  # minutes, then the highest score wins; 0 = until conquest
    biome: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MatchSpec":
        d = dict(d)
        d["players"] = [PlayerSpec(**p) for p in d["players"]]
        return cls(**d)

    def describe(self) -> str:
        vs = " vs ".join(p.label() for p in self.players)
        limit = f", {self.time_limit} min limit" if self.time_limit else ", until conquest"
        return f"{vs} on {self.map} (size {self.size}, seed {self.seed}{limit})"


@dataclass
class MatchResult:
    spec: dict
    status: str  # finished | timeout | failed | incompatible
    reason: str | None = None  # conquest | time_limit
    winners: list[int] = field(default_factory=list)
    game_time: float = 0.0  # s of game time
    wall_time: float = 0.0  # s of real time
    players: list[dict] = field(default_factory=list)  # final stats per player (id 1..n)
    timeline: list[dict] = field(default_factory=list)  # {"time", "scores": {id: score}}
    replay_dir: str | None = None
    errors: list[str] = field(default_factory=list)
    log_tail: list[str] = field(default_factory=list)
    game_log: str | None = None  # the game's interestinglog (errors and warnings)
    # ZADREC lines of recorder AIs (zadbot_rec), from the game's mainlog; not saved with the match
    recording: list[dict] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status == "finished"

    def player(self, pid: int) -> dict:
        for p in self.players:
            if p["id"] == pid:
                return p
        raise KeyError(pid)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("recording")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MatchResult":
        return cls(**d)

    def summary(self) -> str:
        spec = MatchSpec.from_dict(self.spec)
        if not self.finished:
            why = f": {self.errors[0]}" if self.errors else ""
            return f"{self.status} after {self.game_time / 60:.1f} game-min ({self.wall_time:.0f} s real){why}"
        names = {i + 1: p.label() for i, p in enumerate(spec.players)}
        who = ", ".join(f"P{w} {names.get(w, '?')}" for w in self.winners) or "nobody"
        scores = "  ".join(f"P{p['id']} {p['score']:.0f}" for p in self.players)
        return (
            f"{who} won by {self.reason} after {self.game_time / 60:.1f} game-min "
            f"({self.wall_time / 60:.1f} min real)   scores: {scores}"
        )


def outcome(result: MatchResult, pid: int) -> float | None:
    """How well player ``pid`` did, from -1 to +1 (None = no usable result).

    Winning by conquest is +1 and losing -1. When the time limit decides, the
    score margin counts, but at most +-0.5: finishing the game is worth more.
    """
    if not result.finished or not result.players:
        return None
    if result.reason == "conquest":
        if pid in result.winners and len(result.winners) < len(result.players):
            return 1.0
        return -1.0 if pid not in result.winners else 0.0
    me = result.player(pid)["score"]
    others = max(p["score"] for p in result.players if p["id"] != pid)
    margin = (me - others) / max(me + others, 1.0)
    return max(-0.5, min(0.5, 2.0 * margin))


# ------------------------------------------------------------ command line
def build_command(game: GameInstall, spec: MatchSpec, victory: list[str]) -> list[str]:
    kind = spec.map.split("/", 1)[0]
    cmd = [
        *game.command,
        "-mod=public",
        f"-mod={MOD_NAME}",
        "-autostart-nonvisual",
        "-unique-logs",  # logs named by process id, so parallel games keep their own
        f"-autostart={spec.map}",
        "-autostart-player=-1",  # observer: every player is an AI
        f"-autostart-seed={spec.seed}",
        f"-autostart-aiseed={spec.ai_seed}",
    ]
    if kind == "random":
        cmd += [f"-autostart-size={spec.size}", f"-autostart-players={len(spec.players)}"]
        if spec.biome:
            cmd.append(f"-autostart-biome={spec.biome}")
    for i, p in enumerate(spec.players, start=1):
        cmd += [f"-autostart-ai={i}:{p.ai}", f"-autostart-aidiff={i}:{p.difficulty}", f"-autostart-aibehavior={i}:{p.behavior}"]
        cmd.append(f"-autostart-civ={i}:{p.civ or 'random'}")
        if p.team:
            cmd.append(f"-autostart-team={i}:{p.team}")
    cmd += [f"-autostart-victory={v}" for v in victory]
    return cmd


def watch_command(game: GameInstall, spec: MatchSpec, human: int = 0, speed: float = 1.0) -> list[str]:
    """Command line for a match with the game window open: every player an AI
    and you watching (``human=0``), or you playing as player ``human``."""
    kind = spec.map.split("/", 1)[0]
    cmd = [
        *game.command,
        "-mod=public",
        f"-mod={MOD_NAME}",
        f"-autostart={spec.map}",
        f"-autostart-player={human if human else -1}",
        f"-autostart-seed={spec.seed}",
        f"-autostart-aiseed={spec.ai_seed}",
    ]
    if kind == "random":
        cmd += [f"-autostart-size={spec.size}", f"-autostart-players={len(spec.players)}"]
    for i, p in enumerate(spec.players, start=1):
        if i != human:
            cmd += [f"-autostart-ai={i}:{p.ai}", f"-autostart-aidiff={i}:{p.difficulty}",
                    f"-autostart-aibehavior={i}:{p.behavior}"]
        cmd.append(f"-autostart-civ={i}:{p.civ or 'random'}")
    if speed != 1:
        cmd.append(f"-autostart-speed={speed:g}")
    return cmd


def victory_conditions(user_data: Path, spec: MatchSpec) -> list[str]:
    """Conquest plus our report (which also enforces the time limit)."""
    report = ensure_time_limit(user_data, spec.time_limit) if spec.time_limit else "zadbot_report"
    return ["conquest", report]


# ------------------------------------------------------------ result parsing
def score_parts(seq: dict, index: int = -1) -> dict:
    """Summary-screen scores from a metadata.json stats sequence (gui/summary/counters.js)."""
    # every resource type, but not "vegetarianFood" (a subset of food)
    gathered = sum(v[index] for k, v in seq["resourcesGathered"].items() if k != "vegetarianFood")
    economy = round((gathered + seq["tradeIncome"][index]) / 10)
    military = round(
        (seq["enemyUnitsKilledValue"][index] + seq["unitsCapturedValue"][index]
         + seq["enemyBuildingsDestroyedValue"][index] + seq["buildingsCapturedValue"][index]) / 10
    )
    exploration = seq["percentMapExplored"][index] * 10
    return {"economy": economy, "military": military, "exploration": exploration,
            "score": economy + military + exploration}


def _total(counter) -> float:
    return counter["total"][-1] if isinstance(counter, dict) and "total" in counter else 0


def _player_setup(pdata: list, pid: int, n_with_gaia: int) -> dict:
    """Player ``pid``'s entry in the match settings' PlayerData. The game
    setup lists players 1..n; once the match starts, the simulation puts gaia
    first (LoadPlayerSettings in simulation/helpers/Player.js), so in
    metadata.json the index is the player id."""
    idx = pid if len(pdata) >= n_with_gaia else pid - 1
    return (pdata[idx] if 0 <= idx < len(pdata) else None) or {}


def parse_metadata(meta: dict, time_limit: int) -> dict:
    """Result fields from a replay's metadata.json."""
    settings = meta.get("mapSettings", {})
    pdata = settings.get("PlayerData", [])
    players, timeline = [], []
    states = meta.get("playerStates", [])
    for pid, st in enumerate(states):
        if pid == 0:  # gaia
            continue
        seq = st.get("sequences") or {}
        info = _player_setup(pdata, pid, len(states))
        entry = {"id": pid, "ai": info.get("AI") or "", "aiDiff": info.get("AIDiff"),
                 "civ": st.get("civ", info.get("Civ", "")), "state": st.get("state", ""),
                 "pop": st.get("popCount", 0), "phase": st.get("phase", "")}
        if seq:
            entry.update(score_parts(seq))
            entry.update({
                "unitsTrained": _total(seq.get("unitsTrained")),
                "unitsLost": _total(seq.get("unitsLost")),
                "enemyUnitsKilled": _total(seq.get("enemyUnitsKilled")),
                "buildingsLost": _total(seq.get("buildingsLost")),
                "enemyBuildingsDestroyed": _total(seq.get("enemyBuildingsDestroyed")),
            })
        else:
            entry.update({"economy": 0, "military": 0, "exploration": 0, "score": 0})
        players.append(entry)

    # timeline of scores (the game samples every 30 s)
    seqs = {pid: st.get("sequences") for pid, st in enumerate(states) if pid and st.get("sequences")}
    if seqs:
        times = next(iter(seqs.values())).get("time", [])
        for i, t in enumerate(times):
            timeline.append({"time": t, "scores": {pid: score_parts(s, i)["score"] for pid, s in seqs.items()}})

    game_time = meta.get("timeElapsed", 0) / 1000.0
    winners = [p["id"] for p in players if p["state"] == "won"]
    reason = None
    if winners:
        limit_reached = bool(time_limit) and game_time >= time_limit * 60 - 1.0
        reason = "time_limit" if limit_reached else "conquest"
    return {"players": players, "timeline": timeline, "game_time": game_time,
            "winners": winners, "reason": reason}


def read_start_line(replay_dir: Path) -> dict | None:
    try:
        with open(replay_dir / "commands.txt", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return None
    if not first.startswith("start "):
        return None
    try:
        return json.loads(first[len("start "):])
    except json.JSONDecodeError:
        return None


def _matches_spec(attribs: dict, spec: MatchSpec) -> bool:
    s = attribs.get("settings", {})
    ais = [(p or {}).get("AI") or "" for p in s.get("PlayerData", [])]
    return (
        s.get("Seed") == spec.seed
        and s.get("AISeed") == spec.ai_seed
        and attribs.get("map", "").endswith(spec.map)
        and ais[: len(spec.players)] == [p.ai for p in spec.players]
    )


def _replays_of(replays_root: Path, spec: MatchSpec, since: float, hint: str | None = None):
    """Unclaimed replay folders of ``spec`` started after ``since``, newest first.
    Every version folder is searched, because the engine names it after its
    serialization version, which a patch release may keep (lib/build_version.h)."""
    candidates = []
    if hint:
        candidates.append(Path(hint))
    if replays_root.is_dir():
        dirs = [d for d in replays_root.glob("*/*") if d.is_dir() and d.stat().st_mtime >= since - 5]
        candidates += sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)
    for d in candidates:
        if str(d.resolve()) in _CLAIMED:
            continue
        attribs = read_start_line(d)
        if attribs is not None and _matches_spec(attribs, spec):
            yield d


def find_replay(replays_root: Path, spec: MatchSpec, since: float, hint: str | None = None) -> Path | None:
    """The replay folder of this match (``replays/<version>/<date>_<n>/``): the
    newest one started after ``since`` whose settings match, and not already
    taken by a parallel match."""
    with _CLAIM_LOCK:
        for d in _replays_of(replays_root, spec, since, hint):
            _CLAIMED.add(str(d.resolve()))
            return d
    return None


def read_game_log(logs_dir: Path, pid: int) -> tuple[Path | None, list[str]]:
    """The errors and warnings this game process wrote (``-unique-logs``
    names the file interestinglog_<time>_<pid>.html). On Windows this is the
    only place they show up."""
    if not logs_dir.is_dir():
        return None, []
    files = sorted(logs_dir.glob(f"interestinglog_*_{pid}.html"), key=lambda f: f.stat().st_mtime)
    if not files:
        return None, []
    text = files[-1].read_text(encoding="utf-8", errors="replace")
    lines = []
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", text, re.S):
        line = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if line.startswith(("ERROR", "WARNING")):
            lines.append(line)
    return files[-1], lines


def read_recording(logs_dir: Path, pid: int, remove: bool = True) -> list[dict]:
    """The ZADREC lines recorder AIs wrote to this game's mainlog_<time>_<pid>.html
    (on every OS). The file is removed once read (``remove``): it is written
    for every game and grows to a few hundred kB."""
    if not logs_dir.is_dir():
        return []
    records = []
    for f in sorted(logs_dir.glob(f"mainlog_*_{pid}.html"), key=lambda f: f.stat().st_mtime)[-1:]:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"<p>ZADREC (.*?)</p>", text):
            try:
                records.append(json.loads(html.unescape(m.group(1))))
            except json.JSONDecodeError:
                continue
        if remove and records:
            f.unlink(missing_ok=True)
    return records


def parse_stdout_line(line: str, state: dict) -> None:
    """Collect what the game prints (Linux/macOS): progress, errors, ZADBOT events."""
    m = _TURN_RE.match(line)
    if m:
        state["turn"] = int(m.group(1))
        return
    m = _REPLAY_RE.search(line)
    if m:
        state["replay_hint"] = m.group(1)
        return
    if line.startswith("ZADBOT "):
        try:
            event = json.loads(line[len("ZADBOT "):])
        except json.JSONDecodeError:
            return
        state.setdefault("events", []).append(event)
        return
    if "Trying to start with incompatible mods" in line:
        state["incompatible"] = line.strip()
    if "zadbot: unknown Petra settings" in line:
        state["incompatible"] = line.strip()
    if line.startswith("ERROR") or "JavaScript error" in line:
        state.setdefault("errors", []).append(line.strip())


# ------------------------------------------------------------ running
def run_match(
    game: GameInstall,
    spec: MatchSpec,
    user_data: Path,
    timeout: float = 3 * 3600.0,
    on_event: Callable[[dict], None] | None = None,
    startup_timeout: float = 300.0,
) -> MatchResult:
    """Play one match to the end (or the time limit / ``timeout``) and read its result.

    A game that has not started the match (no replay folder) after
    ``startup_timeout`` seconds is stuck and gets stopped. The game is also
    stopped when this function is left early (Ctrl+C, an error): on Windows
    a leftover game keeps the game files locked for every later game."""
    victory = victory_conditions(user_data, spec)
    cmd = build_command(game, spec, victory)
    env = dict(os.environ, ZADBOT_USER_DATA=str(user_data))
    state: dict = {}
    tail: deque[str] = deque(maxlen=40)

    started = time.time()
    # below normal priority on Windows, so the PC stays responsive with many games
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags,
    )

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            n_events = len(state.get("events", []))
            parse_stdout_line(line, state)
            if not _TURN_RE.match(line):
                tail.append(line.rstrip())
            if on_event and len(state.get("events", [])) > n_events:
                on_event(state["events"][-1])

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    timed_out = stuck = False
    match_started = False
    try:
        while True:
            try:
                proc.wait(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            elapsed = time.time() - started
            if elapsed > timeout:
                timed_out = True
                break
            if not match_started and elapsed > startup_timeout:
                match_started = next(_replays_of(user_data / "replays", spec, started), None) is not None
                if not match_started:
                    stuck = True
                    break
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    t.join(timeout=10)
    wall = time.time() - started

    # The game's own log: on Windows the only place its errors appear.
    logs_dir = game.logs_dir or user_data / "logs"
    log_file, log_lines = read_game_log(logs_dir, proc.pid)
    for line in log_lines:
        if line not in state.get("errors", []):
            parse_stdout_line(line, state)

    result = MatchResult(spec=spec.to_dict(), status="failed", wall_time=wall,
                         errors=list(state.get("errors", [])), log_tail=list(tail),
                         game_log=str(log_file) if log_file else None)
    if any(p.ai == RECORDER_AI for p in spec.players):
        result.recording = read_recording(logs_dir, proc.pid)
    result.game_time = state.get("turn", 0) * TURN_SECONDS
    if "incompatible" in state:
        result.status = "incompatible"
        result.errors.append(state["incompatible"])
        return result

    replay = find_replay(user_data / "replays", spec, started, state.get("replay_hint"))
    if replay is not None:
        result.replay_dir = str(replay)
    meta_path = replay / "metadata.json" if replay else None
    if meta_path and meta_path.is_file() and not timed_out:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        parsed = parse_metadata(meta, spec.time_limit)
        active = meta.get("mapSettings", {}).get("VictoryConditions", [])
        if victory[-1] not in active:  # the game drops conditions it cannot find
            result.errors.append(f"victory condition {victory[-1]} was not active: was the zadbot mod loaded?")
        result.players, result.timeline = parsed["players"], parsed["timeline"]
        result.game_time, result.winners, result.reason = parsed["game_time"], parsed["winners"], parsed["reason"]
        result.status = "finished" if result.winners else "failed"
        if not result.winners:
            result.errors.append("the replay has no winner")
    elif stuck:
        result.errors.insert(0, f"the game did not start the match within {startup_timeout:.0f} s, so it was stopped")
    elif timed_out:
        result.status = "timeout"
        result.errors.append(f"stopped after {timeout / 60:.0f} min of real time")
        _fill_from_events(result, state.get("events", []))
    else:
        if replay is None:
            result.errors.insert(0, f"the game exited (code {proc.returncode}) without a replay of this match")
        else:
            result.errors.insert(0, f"the game exited (code {proc.returncode}) before the match ended: "
                                    f"no metadata.json in {replay}")
        warnings = [line for line in log_lines if line.startswith("WARNING")]
        result.errors += warnings[-5:]
        _fill_from_events(result, state.get("events", []))
    return result


def _fill_from_events(result: MatchResult, events: list[dict]) -> None:
    """Best-effort stats from the printed ZADBOT lines when there is no replay result."""
    last = next((e for e in reversed(events) if e.get("players")), None)
    if last:
        result.players = last["players"]
        result.game_time = max(result.game_time, float(last.get("time", 0)))
    result.timeline = [
        {"time": e["time"], "scores": {p["id"]: p["score"] for p in e["players"]}}
        for e in events if e.get("event") == "tick"
    ]

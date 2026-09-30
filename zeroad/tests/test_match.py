import json

import pytest

from zadbot.game import fake_install
from zadbot.match import (
    MatchResult, MatchSpec, PlayerSpec, build_command, find_replay, outcome, parse_metadata,
    parse_stdout_line, score_parts, victory_conditions,
)


def seq(gathered, kills, explored, n=3):
    frac = [i / (n - 1) for i in range(n)]
    return {
        "time": [i * 30.0 for i in range(n)],
        "resourcesGathered": {"food": [f * gathered for f in frac], "wood": [0] * n, "stone": [0] * n,
                              "metal": [0] * n, "vegetarianFood": [f * 99999 for f in frac]},
        "tradeIncome": [0] * n, "enemyUnitsKilledValue": [f * kills for f in frac], "unitsCapturedValue": [0] * n,
        "enemyBuildingsDestroyedValue": [0] * n, "buildingsCapturedValue": [0] * n,
        "percentMapExplored": [f * explored for f in frac],
        "unitsTrained": {"total": [0, 10, 20]},
    }


def metadata(states, time_s, vcs=("conquest",)):
    return {
        "timeElapsed": time_s * 1000,
        "playerStates": [{"name": "Gaia", "state": "active"}] + [
            {"name": f"P{i}", "civ": "athen", "state": st, "popCount": 10, "phase": "town", "sequences": seq(g, k, e)}
            for i, (st, g, k, e) in enumerate(states, start=1)
        ],
        "mapSettings": {"VictoryConditions": list(vcs),
                        "PlayerData": [{"AI": "zadbot_t0", "AIDiff": 3}, {"AI": "petra", "AIDiff": 3}]},
    }


def test_score_parts_matches_summary_screen():
    s = score_parts(seq(10000, 2000, 30))
    assert s == {"economy": 1000, "military": 200, "exploration": 300, "score": 1500}


def test_parse_metadata_conquest():
    meta = metadata([("won", 10000, 2000, 30), ("defeated", 5000, 100, 20)], time_s=25 * 60)
    r = parse_metadata(meta, time_limit=30)
    assert r["winners"] == [1]
    assert r["reason"] == "conquest"
    assert r["game_time"] == 1500
    assert [p["ai"] for p in r["players"]] == ["zadbot_t0", "petra"]
    assert r["players"][0]["score"] == 1500
    assert r["players"][0]["unitsTrained"] == 20
    assert len(r["timeline"]) == 3 and r["timeline"][-1]["scores"][1] == 1500


def test_parse_metadata_time_limit():
    meta = metadata([("defeated", 10000, 0, 0), ("won", 20000, 0, 0)], time_s=30 * 60)
    r = parse_metadata(meta, time_limit=30)
    assert r["winners"] == [2]
    assert r["reason"] == "time_limit"


def test_outcome():
    spec = MatchSpec(players=[PlayerSpec(), PlayerSpec()]).to_dict()
    players = [{"id": 1, "score": 3000}, {"id": 2, "score": 1000}]
    conquest = MatchResult(spec=spec, status="finished", reason="conquest", winners=[1], players=players)
    assert outcome(conquest, 1) == 1.0
    assert outcome(conquest, 2) == -1.0
    limit = MatchResult(spec=spec, status="finished", reason="time_limit", winners=[1], players=players)
    assert outcome(limit, 1) == 0.5  # margin 0.5 -> capped: a time-limit win is worth less than conquest
    close = MatchResult(spec=spec, status="finished", reason="time_limit", winners=[2],
                        players=[{"id": 1, "score": 1000}, {"id": 2, "score": 1100}])
    assert outcome(close, 1) == pytest.approx(-2 * 100 / 2100)
    assert outcome(MatchResult(spec=spec, status="timeout"), 1) is None


def test_build_command(tmp_path):
    game = fake_install()
    spec = MatchSpec(players=[PlayerSpec("zadbot_t1", 4, civ="rome"), PlayerSpec("petra", 4, "defensive")],
                     seed=42, ai_seed=43, time_limit=25)
    vcs = victory_conditions(tmp_path, spec)
    assert vcs == ["conquest", "zadbot_limit_25"]
    vc_file = tmp_path / "mods/zadbot/simulation/data/settings/victory_conditions/zadbot_limit_25.json"
    assert json.loads(vc_file.read_text())["Data"]["Scripts"] == ["scripts/ZadbotReport.js"]
    cmd = build_command(game, spec, vcs)
    for arg in ["-mod=public", "-mod=zadbot", "-autostart-nonvisual", "-autostart=random/mainland",
                "-autostart-player=-1", "-autostart-seed=42", "-autostart-aiseed=43", "-autostart-size=128",
                "-autostart-players=2", "-autostart-ai=1:zadbot_t1", "-autostart-aidiff=1:4",
                "-autostart-civ=1:rome", "-autostart-ai=2:petra", "-autostart-aibehavior=2:defensive",
                "-autostart-civ=2:random", "-autostart-victory=conquest", "-autostart-victory=zadbot_limit_25"]:
        assert arg in cmd
    no_limit = MatchSpec(players=spec.players, time_limit=0)
    assert victory_conditions(tmp_path, no_limit) == ["conquest", "zadbot_report"]


def test_find_replay_matches_settings_and_claims_once(tmp_path):
    spec = MatchSpec(players=[PlayerSpec("zadbot_t0"), PlayerSpec("petra")], seed=5, ai_seed=6)
    other = MatchSpec(players=[PlayerSpec("zadbot_t1"), PlayerSpec("petra")], seed=5, ai_seed=6)

    def replay(name, s):
        d = tmp_path / "0.28.0" / name
        d.mkdir(parents=True)
        attribs = {"map": "maps/" + s.map, "settings": {"Seed": s.seed, "AISeed": s.ai_seed,
                                                        "PlayerData": [{"AI": p.ai} for p in s.players]}}
        (d / "commands.txt").write_text("start " + json.dumps(attribs) + "\nturn 0 200\n")
        return d

    mine = replay("2026-01-01_0001", spec)
    replay("2026-01-01_0002", other)
    assert find_replay(tmp_path, spec, since=0) == mine
    assert find_replay(tmp_path, spec, since=0) is None  # already taken


def test_parse_stdout_line():
    state: dict = {}
    parse_stdout_line("Turn 150 (200)...\n", state)
    parse_stdout_line("FILES| Replay written to '/x/replays/0.28.0/2026_0001'\n", state)
    parse_stdout_line('ZADBOT {"event":"tick","time":60,"players":[]}\n', state)
    parse_stdout_line("ERROR: Trying to start with incompatible mods: zadbot.\n", state)
    assert state["turn"] == 150
    assert state["replay_hint"] == "/x/replays/0.28.0/2026_0001"
    assert state["events"][0]["event"] == "tick"
    assert "incompatible" in state


def test_read_game_log(tmp_path):
    from zadbot.match import read_game_log

    (tmp_path / "interestinglog_100_42.html").write_text(
        "<html><body><h1>Pyrogenesis Log</h1>\n"
        '<p class="error">ERROR: JavaScript error: simulation/ai/zadbot_t0/_zadbot.js line 1\n'
        "SyntaxError: &lt;oops&gt;</p>\n"
        '<p class="warning">WARNING: PlayerID 1 |   zadbot: unknown Petra settings: Economy.x</p>\n'
        "<p>Engine exited successfully on 2026-10-01</p>\n"
    )
    (tmp_path / "interestinglog_100_7.html").write_text('<p class="error">ERROR: other game</p>')
    path, lines = read_game_log(tmp_path, 42)
    assert path.name == "interestinglog_100_42.html"
    assert lines[0].startswith("ERROR: JavaScript error") and "SyntaxError: <oops>" in lines[0]
    assert lines[1].endswith("unknown Petra settings: Economy.x")
    assert len(lines) == 2
    assert read_game_log(tmp_path, 99) == (None, [])
    assert read_game_log(tmp_path / "missing", 42) == (None, [])
    state: dict = {}
    for line in lines:
        parse_stdout_line(line, state)
    assert "incompatible" in state and state["errors"][0].startswith("ERROR: JavaScript")

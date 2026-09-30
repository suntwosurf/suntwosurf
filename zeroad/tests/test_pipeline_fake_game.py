"""The whole pipeline (subprocess, replay files, slots, learning) on the fake
game from zadbot.fakegame. The fake's matches are pretend: these tests check
the plumbing, not how well the bot plays 0 A.D."""

import json
import shutil
from pathlib import Path

import pytest

from zadbot.cli import main
from zadbot.game import fake_install
from zadbot.learner import CrossEntropyLearner
from zadbot.match import MatchSpec, PlayerSpec, run_match
from zadbot.modinstall import ensure_installed
from zadbot.pipeline import IncompatibleGame, MatchRunner, evaluate, finish_test, learn


def runner(cfg, tmp_path, game=None):
    user = Path(cfg.game.user_data)
    ensure_installed(user)
    return MatchRunner(cfg, game or fake_install(), user, tmp_path / "runs", lambda m: None)


def test_run_match_reads_replay(fake_cfg, tmp_path):
    user = Path(fake_cfg.game.user_data)
    ensure_installed(user)
    spec = MatchSpec(players=[PlayerSpec("petra", 5), PlayerSpec("petra", 1)], seed=3, ai_seed=3, time_limit=0)
    events = []
    r = run_match(fake_install(), spec, user, timeout=60, on_event=events.append)
    assert r.finished and r.reason == "conquest" and r.winners == [1]
    assert [(p["ai"], p["aiDiff"]) for p in r.players] == [("petra", 5), ("petra", 1)]
    assert r.replay_dir and (Path(r.replay_dir) / "metadata.json").is_file()
    assert Path(r.replay_dir).parent == user / "replays" / "0.28.0"
    assert r.players[0]["score"] > r.players[1]["score"]
    assert r.timeline and events[-1]["event"] == "end"


def test_finish_test(fake_cfg, tmp_path):
    rs = finish_test(runner(fake_cfg, tmp_path), PlayerSpec("petra", 5), PlayerSpec("petra", 1), 3,
                     "random/mainland", 128, 1)
    assert [r.reason for r in rs] == ["conquest"] * 3
    assert all(r.winners == [1] for r in rs)
    assert len({r.replay_dir for r in rs}) == 3
    assert len(list((tmp_path / "runs" / "matches").glob("*.json"))) == 3


def test_learning_improves_on_fake_game(fake_cfg, tmp_path):
    cfg = fake_cfg
    lr = CrossEntropyLearner(cfg.learn, cfg.match)
    state = tmp_path / "runs" / "learner.json"
    learn(cfg, runner(cfg, tmp_path), lr, 6, state, lambda m: None)
    assert state.is_file()
    ev = evaluate(cfg, runner(cfg, tmp_path), {"learned": lr.centre_values(), "petra": "petra"}, 8, seed=1)
    assert ev["learned"]["finished"] == 8
    assert ev["learned"]["mean_outcome"] > ev["petra"]["mean_outcome"]
    # resume continues from the saved generation
    back = CrossEntropyLearner.load(state, cfg.learn, cfg.match)
    assert back.generation == 6


def test_wrong_game_version_stops_learning(fake_cfg, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(fake_install().data_dir, data)
    mod_json = data / "mods" / "public" / "mod.json"
    meta = json.loads(mod_json.read_text())
    meta["version"] = "0.27.1"
    mod_json.write_text(json.dumps(meta))
    game = fake_install(data)
    assert game.problems()
    # even when forced past the Python check, the game itself refuses the mod
    lr = CrossEntropyLearner(fake_cfg.learn, fake_cfg.match)
    with pytest.raises(IncompatibleGame, match="incompatible mods: zadbot"):
        learn(fake_cfg, runner(fake_cfg, tmp_path, game), lr, 1, tmp_path / "l.json", lambda m: None)


def test_cli_on_fake_game(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["check", "--game", "fake"]) == 0
    assert "OK: this game version" in capsys.readouterr().out
    assert main(["finish-test", "--game", "fake", "--games", "2", "--workers", "2"]) == 0
    assert "2/2 games played to the end" in capsys.readouterr().out
    assert main(["learn", "--game", "fake", "--generations", "2", "--workers", "3"]) == 0
    assert main(["evaluate", "--game", "fake", "--matches", "4", "--baseline", "--workers", "3"]) == 0
    out = capsys.readouterr().out
    assert "learned" in out and "petra" in out and "map set 1" in out
    assert "learned vs petra on the same maps" in out
    assert main(["evaluate", "--game", "fake", "--matches", "2", "--seed", "77", "--workers", "2"]) == 0
    assert "map set 77" in capsys.readouterr().out
    assert main(["show"]) == 0
    assert "learned settings" in capsys.readouterr().out
    assert main(["evaluate", "--game", "fake", "--matches", "4", "--settings", "rush,boom,learned",
                 "--workers", "3"]) == 0
    out = capsys.readouterr().out
    assert "evaluating petra, rush, boom, learned" in out
    assert "each vs petra on the same maps" in out and "Would picking a style per civ help?" in out
    saved = json.loads((tmp_path / "runs/styles.json").read_text())
    assert list(saved["variants"]) == ["petra", "rush", "boom", "learned"]
    assert len(saved["variants"]["rush"]["outcomes"]) == 4
    assert main(["show"]) == 0
    assert "Does the style change who wins?" in capsys.readouterr().out
    assert main(["evaluate", "--game", "fake", "--settings", "blitz"]) == 1
    assert "unknown setting(s) blitz" in capsys.readouterr().out
    assert main(["install-mod", "--game", "fake", "--remove-slots"]) == 0
    params = tmp_path / "runs/fake-user-data/mods/zadbot/simulation/ai/zadbot/params.js"
    assert "personality.aggressive" in params.read_text()
    assert not list((tmp_path / "runs/fake-user-data/mods/zadbot/simulation/ai").glob("zadbot_t*"))


# --- Windows: the game prints nothing to the console, only its log file tells
def test_windows_finished_match_without_console(fake_cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("ZADBOT_FAKE_WINDOWS", "1")
    user = Path(fake_cfg.game.user_data)
    ensure_installed(user)
    spec = MatchSpec(players=[PlayerSpec("petra", 5), PlayerSpec("petra", 1)], seed=4, ai_seed=4, time_limit=0)
    r = run_match(fake_install(), spec, user, timeout=60)
    assert r.finished and r.winners == [1] and r.log_tail == []


def test_windows_failure_is_explained(fake_cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("ZADBOT_FAKE_WINDOWS", "1")
    monkeypatch.setenv("ZADBOT_FAKE_FAIL", "Failed to load map <random/mainland>")
    user = Path(fake_cfg.game.user_data)
    ensure_installed(user)
    spec = MatchSpec(players=[PlayerSpec("petra", 5), PlayerSpec("petra", 1)], seed=5, ai_seed=5, time_limit=0)
    r = run_match(fake_install(), spec, user, timeout=60)
    assert r.status == "failed"
    assert "without a replay" in r.errors[0]
    assert "ERROR: Failed to load map <random/mainland>" in r.errors
    assert r.game_log and Path(r.game_log).name.startswith("interestinglog_")
    assert "s real" in r.summary()


def test_windows_incompatible_version_from_log(fake_cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("ZADBOT_FAKE_WINDOWS", "1")
    data = tmp_path / "data"
    shutil.copytree(fake_install().data_dir, data)
    mod_json = data / "mods" / "public" / "mod.json"
    mod_json.write_text(json.dumps(dict(json.loads(mod_json.read_text()), version="0.27.1")))
    lr = CrossEntropyLearner(fake_cfg.learn, fake_cfg.match)
    with pytest.raises(IncompatibleGame, match="incompatible mods: zadbot"):
        learn(fake_cfg, runner(fake_cfg, tmp_path, fake_install(data)), lr, 1, tmp_path / "l.json", lambda m: None)


def test_cli_explains_refused_mod(tmp_path, monkeypatch, capsys):
    """finish-test on a game that refuses the mod: a message, not a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZADBOT_FAKE_WINDOWS", "1")
    assert main(["install-mod", "--game", "fake"]) == 0
    # the Python check passes (0.28.0), but the game refuses this mod.json
    mod_json = tmp_path / "runs/fake-user-data/mods/zadbot/mod.json"
    monkeypatch.setattr("zadbot.modinstall.ensure_installed", lambda user_data: None)
    mod_json.write_text(json.dumps(dict(json.loads(mod_json.read_text()), dependencies=["0ad=0.27.0"])))
    capsys.readouterr()
    assert main(["finish-test", "--game", "fake", "--games", "1", "--workers", "1"]) == 1
    out = capsys.readouterr().out
    assert "did not accept the zadbot mod" in out and "incompatible mods: zadbot" in out


def test_stuck_game_is_stopped(fake_cfg, monkeypatch):
    import time

    monkeypatch.setenv("ZADBOT_FAKE_HANG", "60")
    user = Path(fake_cfg.game.user_data)
    ensure_installed(user)
    spec = MatchSpec(players=[PlayerSpec("petra", 5), PlayerSpec("petra", 1)], seed=6, ai_seed=6, time_limit=0)
    t0 = time.time()
    r = run_match(fake_install(), spec, user, timeout=60, startup_timeout=1.5)
    assert time.time() - t0 < 20
    assert r.status == "failed" and "did not start the match" in r.errors[0]


def test_windows_one_worker_refuses_next_to_running_game(tmp_path, monkeypatch):
    from zadbot import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "one_game_at_a_time", lambda game: True)
    monkeypatch.setattr(cli, "running_game_processes", lambda: [(4321, r"C:\\0ad\\binaries\\system\\pyrogenesis.exe")])
    with pytest.raises(SystemExit, match="4321") as e:
        main(["finish-test", "--game", "fake", "--games", "1", "--workers", "1"])
    assert "Stop-Process -Name pyrogenesis" in str(e.value)


def test_windows_parallel_games_use_own_copies(tmp_path, monkeypatch, capsys):
    """Windows, --workers 3: each worker plays in its own copy (-writableRoot),
    even while the normal game runs; leftovers inside the copies block."""
    from zadbot import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "one_game_at_a_time", lambda game: True)
    playing = (111, str(tmp_path / "0ad" / "binaries" / "system" / "pyrogenesis.exe"))
    monkeypatch.setattr(cli, "running_game_processes", lambda: [playing])
    assert main(["finish-test", "--game", "fake", "--games", "3", "--workers", "3"]) == 0
    out = capsys.readouterr().out
    assert "making 3 game copies" in out and "3/3 games played to the end" in out
    copies = tmp_path / "runs" / "game-copies"
    for i in range(3):
        data = copies / f"worker{i}" / "binaries" / "data"
        assert (data / "mods" / "zadbot" / "mod.json").is_file()  # mod installed into the copy
    replays = list(copies.glob("worker*/binaries/data/replays/*/*/metadata.json"))
    assert len(replays) == 3  # the games ran in the copies ...
    assert not (tmp_path / "runs" / "fake-user-data" / "replays").exists()  # ... not in the normal folders

    # the copies are reused
    assert main(["finish-test", "--game", "fake", "--games", "3", "--workers", "3", "--seed", "7"]) == 0
    assert "making" not in capsys.readouterr().out

    leftover = (222, str(copies / "worker1" / "binaries" / "system" / "pyrogenesis.exe"))
    monkeypatch.setattr(cli, "running_game_processes", lambda: [playing, leftover])
    with pytest.raises(SystemExit, match="Stop-Process -Id 222"):
        main(["finish-test", "--game", "fake", "--games", "1", "--workers", "3"])


def test_watch_installs_learned_bot_and_opens_the_game(tmp_path, monkeypatch, capsys):
    from zadbot import cli
    from zadbot.params import parse_params_js

    monkeypatch.chdir(tmp_path)
    assert main(["learn", "--game", "fake", "--generations", "1", "--workers", "3"]) == 0
    capsys.readouterr()
    assert main(["watch", "--game", "fake", "--civ", "rome", "--seed", "7", "--speed", "5"]) == 0
    out = capsys.readouterr().out
    assert "learned settings after 1 generations" in out
    for arg in ["-mod=zadbot", "-autostart=random/mainland", "-autostart-player=-1", "-autostart-ai=1:zadbot",
                "-autostart-ai=2:petra", "-autostart-civ=1:rome", "-autostart-civ=2:rome", "-autostart-seed=7",
                "-autostart-speed=5"]:
        assert arg in out
    assert "-autostart-nonvisual" not in out  # a window, not a headless run
    params = tmp_path / "runs/fake-user-data/mods/zadbot/simulation/ai/zadbot/params.js"
    assert parse_params_js(params.read_text())  # the learned settings, not plain Petra

    assert main(["watch", "--game", "fake", "--play"]) == 0
    out = capsys.readouterr().out
    assert "-autostart-player=2" in out and "-autostart-ai=1:zadbot" in out and "-autostart-ai=2:" not in out

    # Windows: refuses while the normal game is open, not because of training copies
    monkeypatch.setattr(cli, "one_game_at_a_time", lambda game: True)
    copy = str(tmp_path / "runs" / "game-copies" / "worker3" / "binaries" / "system" / "pyrogenesis.exe")
    monkeypatch.setattr(cli, "running_game_processes", lambda: [(5, copy)])
    assert main(["watch", "--game", "fake"]) == 0
    monkeypatch.setattr(cli, "running_game_processes", lambda: [(5, copy), (6, r"C:\0ad\binaries\system\pyrogenesis.exe")])
    with pytest.raises(SystemExit, match="already open"):
        main(["watch", "--game", "fake"])


def test_record_teacher_games(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["record", "--game", "fake", "--games", "3", "--workers", "2", "--difficulties", "2,5"]) == 0
    out = capsys.readouterr().out
    assert "recorded 3 games" in out and "Petra's decisions:" in out
    files = sorted((tmp_path / "runs/record").glob("*.jsonl"))
    assert [f.name for f in files] == ["1-0.jsonl", "1-1.jsonl", "1-2.jsonl"]
    lines = [json.loads(line) for line in files[0].read_text().splitlines()]
    game = lines[0]["game"]
    assert [p["ai"] for p in game["spec"]["players"]] == ["zadbot_rec", "zadbot_rec"]
    assert {p["difficulty"] for p in game["spec"]["players"]} <= {2, 5} and game["winners"]
    headers = [r for r in lines[1:] if r.get("header")]
    assert sorted(h["p"] for h in headers) == [1, 2] and "income" in headers[0]["features"]
    assert sum("t" in r for r in lines) > 100
    # the game's main logs are read and removed; the match files don't carry the recording
    assert not list((tmp_path / "runs/fake-user-data/logs").glob("mainlog_*"))
    match = json.loads(next((tmp_path / "runs/record/matches").glob("*.json")).read_text())
    assert "recording" not in match
    # the same seed resumes: nothing left to play
    assert main(["record", "--game", "fake", "--games", "3", "--workers", "2"]) == 0
    assert "3 of 3 games already recorded" in capsys.readouterr().out

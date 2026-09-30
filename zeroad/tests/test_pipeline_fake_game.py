"""The whole pipeline (subprocess, replay files, slots, learning) on the fake
game from zadbot.fakegame. The fake's matches are pretend: these tests check
the plumbing, not how well the bot plays 0 A.D."""

import dataclasses
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
    assert "learned" in out and "petra" in out
    assert main(["show"]) == 0
    assert "learned settings" in capsys.readouterr().out
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

"""Command line: ``zadbot <command> --help`` for details.

check -> install-mod -> finish-test (does the bot finish a game?) -> learn -> evaluate -> show
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import GAME_VERSION
from .config import BotConfig, load_config
from .game import GameInstall, find_game, one_game_at_a_time, running_game_processes, user_data_dir


def log(msg: str) -> None:
    print(msg, flush=True)


def _config(args) -> BotConfig:
    cfg = load_config(args.config) if args.config else BotConfig()
    game = cfg.game
    if args.game is not None:
        game = dataclasses.replace(game, path=args.game)
    if args.user_data is not None:
        game = dataclasses.replace(game, user_data=args.user_data)
    if args.workers is not None:
        game = dataclasses.replace(game, workers=args.workers)
    return dataclasses.replace(cfg, game=game)


def _user_data(cfg: BotConfig, game: GameInstall) -> Path:
    if game.fake and not cfg.game.user_data:
        return Path("runs") / "fake-user-data"  # keep the real game's folder clean
    return user_data_dir(cfg.game.user_data)


def _game(cfg: BotConfig) -> tuple[GameInstall, Path]:
    try:
        game = find_game(cfg.game.path)
    except FileNotFoundError as e:
        raise SystemExit(str(e)) from None
    problems = game.problems()
    if problems:
        raise SystemExit("\n".join(["cannot run:"] + ["  " + p for p in problems]))
    return game, _user_data(cfg, game)


def _playing(cfg: BotConfig) -> tuple[BotConfig, GameInstall, Path]:
    """_game() for commands that start games. On Windows only one game can
    run at a time (see game.one_game_at_a_time), so refuse to start next to
    a running one and use one worker."""
    game, user_data = _game(cfg)
    if one_game_at_a_time(game):
        running = running_game_processes()
        if running:
            raise SystemExit(
                f"0 A.D. is already running (pyrogenesis.exe, process {', '.join(map(str, running))}).\n"
                "On Windows a second copy cannot read the game files while it runs. Close the game,\n"
                "or end games left over from an earlier run with:  Stop-Process -Name pyrogenesis -Force"
            )
        if cfg.game.workers > 1:
            log("note: on Windows 0 A.D. locks its game files, so zadbot runs one game at a time")
            cfg = dataclasses.replace(cfg, game=dataclasses.replace(cfg.game, workers=1))
    return cfg, game, user_data


def _refused(e: Exception, user_data: Path) -> int:
    from .modinstall import mod_dir

    log(f"stopped: the game did not accept the zadbot mod: {e}")
    log(f"  The mod is in {mod_dir(user_data)}. zadbot check shows the game version; if your game keeps")
    log("  its mods in another folder, pass --user-data <the folder that contains mods\\>.")
    return 1


def _runner(cfg: BotConfig, game: GameInstall, user_data: Path, out: Path | None):
    from .modinstall import ensure_installed
    from .pipeline import MatchRunner

    ensure_installed(user_data)
    return MatchRunner(cfg, game, user_data, out, log)


# ------------------------------------------------------------------ commands
def cmd_check(args) -> int:
    from .modinstall import mod_dependency, mod_dir

    cfg = _config(args)
    try:
        game = find_game(cfg.game.path)
    except FileNotFoundError as e:
        log(str(e))
        return 1
    user_data = _user_data(cfg, game)
    log(f"game:       {' '.join(game.command)}")
    log(f"data:       {game.data_dir}")
    log(f"version:    {game.version}   (this bot: {GAME_VERSION}, mod requires {mod_dependency()})")
    log(f"Petra:      {'found' if game.has_petra() else 'MISSING'}")
    log(f"user data:  {user_data}")
    installed = (mod_dir(user_data) / "mod.json").is_file()
    log(f"zadbot mod: {'installed in ' + str(mod_dir(user_data)) if installed else 'not installed (zadbot install-mod)'}")
    for note in game.notes:
        log(f"note:       {note}")
    problems = game.problems()
    for p in problems:
        log(f"PROBLEM:    {p}")
    if not problems:
        log("OK: this game version and its Petra work with zadbot")
    return 1 if problems else 0


def cmd_install_mod(args) -> int:
    from .learner import load_centre
    from .modinstall import install_mod, remove_slots
    from .params import describe

    cfg = _config(args)
    _, user_data = _game(cfg)
    learned, comment = None, "plain Petra"
    if not args.plain and Path(args.state).is_file():
        learned = load_centre(args.state)
        comment = f"learned: {args.state}"
    dest = install_mod(user_data, learned, comment)
    removed = remove_slots(user_data) if args.remove_slots else 0
    log(f"installed {dest}  ({comment})")
    if learned:
        log("\n".join("  " + line for line in describe(learned)))
    if removed:
        log(f"removed {removed} training slot AI(s)")
    log("In the game: Settings > Mod Selection > enable 'zadbot', then pick 'zadbot (learned Petra)' as an AI.")
    return 0


def _print_result(result) -> None:
    log(result.summary())
    for p in result.players:
        log(f"  P{p['id']} {p.get('ai', ''):10s} {p.get('civ', ''):6s} {p.get('state', ''):9s} score {p['score']:.0f} "
            f"(economy {p['economy']:.0f}, military {p['military']:.0f}, exploration {p['exploration']:.0f})")
    if result.replay_dir:
        log(f"  replay: {result.replay_dir}  (watch it in the game: Replays)")
    for e in result.errors[:10]:
        log(f"  error: {e}")
    if result.game_log and not result.finished:
        log(f"  game log: {result.game_log}")
    if not result.finished and result.log_tail:
        log("  last output:")
        log("\n".join("    " + line for line in result.log_tail[-15:]))


def cmd_match(args) -> int:
    from .match import MatchSpec, PlayerSpec
    from .pipeline import IncompatibleGame

    cfg = _config(args)
    cfg, game, user_data = _playing(cfg)
    players = [PlayerSpec.parse(args.p1), PlayerSpec.parse(args.p2)]
    players[0].civ, players[1].civ = args.civ1, args.civ2
    spec = MatchSpec(players=players, map=args.map, size=args.size, seed=args.seed, ai_seed=args.seed,
                     time_limit=args.limit)
    log(f"match: {spec.describe()}")
    runner = _runner(dataclasses.replace(cfg, game=dataclasses.replace(cfg.game, workers=1)), game, user_data, Path(args.out))
    from .pipeline import Job

    try:
        result = runner.run_one(Job(spec=spec, tag={"match": args.seed}))
    except IncompatibleGame as e:
        return _refused(e, user_data)
    _print_result(result)
    return 0 if result.finished else 1


def cmd_finish_test(args) -> int:
    from .match import PlayerSpec
    from .pipeline import IncompatibleGame, finish_test

    cfg = _config(args)
    cfg, game, user_data = _playing(cfg)
    bot, opp = PlayerSpec.parse(args.bot), PlayerSpec.parse(args.opponent)
    log(f"finish test: {bot.label()} vs {opp.label()}, {args.games} full game(s) on {args.map} "
        f"(size {args.size}), no time limit, {cfg.game.workers} at a time")
    runner = _runner(cfg, game, user_data, Path(args.out))
    try:
        results = finish_test(runner, bot, opp, args.games, args.map, args.size, args.seed)
    except IncompatibleGame as e:
        return _refused(e, user_data)
    finished = [r for r in results if r.finished and r.reason == "conquest"]
    bot_wins = [r for r in finished if 1 in r.winners]
    log("")
    for r in results:
        _print_result(r)
    log("")
    log(f"{len(finished)}/{len(results)} games played to the end (conquest); "
        f"{bot.label()} won {len(bot_wins)}")
    if finished:
        mins = [r.game_time / 60 for r in finished]
        walls = [r.wall_time / 60 for r in finished]
        log(f"game length {min(mins):.1f}-{max(mins):.1f} game-min, {min(walls):.1f}-{max(walls):.1f} min real time each")
    return 0 if len(finished) == len(results) else 1


def cmd_learn(args) -> int:
    from .learner import CrossEntropyLearner
    from .pipeline import IncompatibleGame, learn

    cfg = _config(args)
    cfg, game, user_data = _playing(cfg)
    state = Path(args.state)
    if state.is_file():
        learner = CrossEntropyLearner.load(state, cfg.learn, cfg.match)
        log(f"resuming {state} at generation {learner.generation}")
    else:
        learner = CrossEntropyLearner(cfg.learn, cfg.match)
    m = cfg.match
    log(f"learning vs {m.opponent} (difficulty {m.difficulty}) on {', '.join(m.maps)} size {m.size}, "
        f"{'time limit ' + str(m.time_limit) + ' min' if m.time_limit else 'until conquest'}, {cfg.game.workers} games at a time")
    runner = _runner(cfg, game, user_data, state.parent)
    try:
        learn(cfg, runner, learner, args.generations, state, log)
    except IncompatibleGame as e:
        return _refused(e, user_data)
    except KeyboardInterrupt:
        log(f"stopped; finished generations are saved in {state}")
        return 130
    from .report import learning_report

    log("\n".join(learning_report(learner.to_dict())))
    log("next: zadbot evaluate   (then zadbot install-mod to play against it)")
    return 0


def cmd_evaluate(args) -> int:
    from .learner import load_centre
    from .pipeline import IncompatibleGame, evaluate
    from .report import evaluation_report

    cfg = _config(args)
    cfg, game, user_data = _playing(cfg)
    variants: dict = {}
    if Path(args.state).is_file():
        variants["learned"] = load_centre(args.state)
    elif not args.baseline:
        log(f"no learner state at {args.state}: run zadbot learn first, or use --baseline")
        return 1
    if args.baseline:
        variants["petra"] = "petra"
    n = args.matches or cfg.evaluate.matches
    m = cfg.match
    log(f"evaluating {', '.join(variants)} vs {m.opponent} (difficulty {m.difficulty}): {n} matches each, "
        f"seeds from {cfg.evaluate.seed} (not used in training)")
    runner = _runner(cfg, game, user_data, Path(args.state).parent)
    try:
        results = evaluate(cfg, runner, variants, n, cfg.evaluate.seed)
    except IncompatibleGame as e:
        return _refused(e, user_data)
    out = Path(args.state).parent / "evaluate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    log("\n".join(evaluation_report(results)))
    log(f"saved {out}")
    return 0


def cmd_show(args) -> int:
    from .report import learning_report

    state = Path(args.state)
    if not state.is_file():
        log(f"no learner state at {state}")
        return 1
    log("\n".join(learning_report(json.loads(state.read_text(encoding="utf-8")))))
    ev = state.parent / "evaluate.json"
    if ev.is_file():
        from .report import evaluation_report

        log("")
        log("\n".join(evaluation_report(json.loads(ev.read_text(encoding="utf-8")))))
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zadbot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="TOML config (see configs/default.toml)")
    common.add_argument("--game", help='pyrogenesis(.exe) or the 0 A.D. folder; "fake" = stand-in without the game')
    common.add_argument("--user-data", help="0 A.D. user data folder (mods, replays), if not the default")
    common.add_argument("--workers", type=int, help="matches at a time")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("check", parents=[common], help="find the game, check its version and Petra")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("install-mod", parents=[common], help="install the zadbot mod (with the learned settings)")
    s.add_argument("--state", default="runs/learner.json")
    s.add_argument("--plain", action="store_true", help="install with plain Petra settings")
    s.add_argument("--remove-slots", action="store_true", help="delete the training slot AIs")
    s.set_defaults(func=cmd_install_mod)

    s = sub.add_parser("match", parents=[common], help="play one match, e.g. --p1 zadbot:3 --p2 petra:3")
    s.add_argument("--p1", default="petra:3", help="ai[:difficulty[:behavior]]")
    s.add_argument("--p2", default="petra:3")
    s.add_argument("--civ1", default="")
    s.add_argument("--civ2", default="")
    s.add_argument("--map", default="random/mainland")
    s.add_argument("--size", type=int, default=128)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--limit", type=int, default=0, help="minutes, then the score decides (0 = until conquest)")
    s.add_argument("--out", default="runs")
    s.set_defaults(func=cmd_match)

    s = sub.add_parser("finish-test", parents=[common], help="full games without time limit: does the bot finish the game?")
    s.add_argument("--bot", default="petra:5", help="ai[:difficulty[:behavior]] of player 1")
    s.add_argument("--opponent", default="petra:1")
    s.add_argument("--games", type=int, default=2)
    s.add_argument("--map", default="random/mainland")
    s.add_argument("--size", type=int, default=128)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", default="runs")
    s.set_defaults(func=cmd_finish_test)

    s = sub.add_parser("learn", parents=[common], help="learn Petra's settings by playing against Petra")
    s.add_argument("--generations", type=int, default=5)
    s.add_argument("--state", default="runs/learner.json")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("evaluate", parents=[common], help="learned bot vs Petra on fresh seeds")
    s.add_argument("--state", default="runs/learner.json")
    s.add_argument("--matches", type=int, default=0)
    s.add_argument("--baseline", action="store_true", help="also plain Petra vs Petra on the same seeds")
    s.set_defaults(func=cmd_evaluate)

    s = sub.add_parser("show", help="learning progress and learned settings")
    s.add_argument("--state", default="runs/learner.json")
    s.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

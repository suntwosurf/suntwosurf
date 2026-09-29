"""Command line: ``aorbot <command> --help`` for details.

Offline:   sim-demo, fake-game, rl-train
Real game: check-telemetry -> record + build-line (or import-path) -> test-reset -> learn -> report -> drive
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from .config import BotConfig, load_config


def log(msg: str) -> None:
    print(msg, flush=True)


def _config(path: str | None) -> BotConfig:
    return load_config(path) if path else BotConfig()


# ---------------------------------------------------------------- offline
def cmd_sim_demo(args) -> int:
    from .line import RacingLine
    from .pipeline import drive_best, learn, make_learner, resolve_grip, sim_config, simulate_demo
    from .record import build_line, save_trace
    from .sim.io import SimIO
    from .sim.stage import make_stage

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stage = make_stage(args.seed, length=args.length)
    cfg = sim_config(stage, args.weather, _config(args.config))
    cfg = dataclasses.replace(cfg, learn=dataclasses.replace(cfg.learn, init_scale=args.init_scale))
    log(f"simulated stage seed={args.seed}: {stage.centerline.length:.0f} m, weather={args.weather}")

    log("1) demonstration: a careful simulated 'human' run, recorded like real telemetry")
    trace = simulate_demo(stage, args.weather, cfg, seed=args.seed)
    save_trace(out / "demo_trace.npz", trace)
    line, grip = build_line(trace)
    line.save(out / "line.npz")
    line = RacingLine.load(out / "line.npz")
    log(f"   demo time {line.meta['demo_time']:.2f}s, reference line {line.length:.0f} m, grip used {grip:.2f} m/s^2")

    cfg = resolve_grip(cfg, line, log)
    learner = make_learner(line, cfg)
    log(f"2) learning: {args.iterations} attempts, starting at {args.init_scale:.2f} x the demo's corner speeds")
    io = SimIO(stage, args.weather)
    results = learn(io, line, cfg, learner, args.iterations, save_path=out / "learner.json", log=lambda m: log("   " + m))
    if learner.best is None:
        log("   no finished attempt yet - run more iterations")
        return 1
    from .report import learning_verdict

    _print_verdict(learning_verdict(learner.history))
    final = drive_best(io, line, cfg, learner)
    log(f"3) drive with the best profile: {final.status.value}, {final.time:.2f}s "
        f"(demo {line.meta['demo_time']:.2f}s, first finish "
        f"{next(r.time for r in results if r.finished):.2f}s)")
    if not args.no_plot:
        try:
            from .plot import plot_learning

            plot_learning(out / "learning.png", line, learner, results, road_half_width=stage.half_width,
                          title=f"sim stage {args.seed} | {args.weather} | {len(results)} attempts")
            log(f"plot: {out / 'learning.png'}")
        except ImportError:
            log("(pip install matplotlib for the plot)")
    return 0


def cmd_fake_game(args) -> int:
    from .pipeline import sim_config, simulate_demo
    from .record import build_line
    from .sim.io import FakeGame
    from .sim.stage import make_stage

    cfg = _config(args.config)
    stage = make_stage(args.seed, length=args.length)
    if args.write_line:
        line, grip = build_line(simulate_demo(stage, args.weather, sim_config(stage, args.weather), seed=args.seed))
        line.save(args.write_line)
        log(f"wrote {args.write_line} ({line.length:.0f} m, grip {grip:.2f}); "
            f"use rules.max_offset <= {stage.crash_offset:.1f} for this stage")
    game = FakeGame(stage, args.weather, telemetry_addr=(cfg.telemetry.host, cfg.telemetry.port),
                    control_addr=(cfg.controls.udp_host, cfg.controls.udp_port), fmt=cfg.telemetry.format,
                    time_scale=args.time_scale)
    log(f"fake game: stage seed {args.seed} ({stage.centerline.length:.0f} m), sending {cfg.telemetry.format} "
        f"telemetry to {cfg.telemetry.host}:{cfg.telemetry.port}, controls on udp {cfg.controls.udp_port}. Ctrl+C stops.")
    game.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        game.stop()
    return 0


# -------------------------------------------------------------- real game
def _open_game(cfg: BotConfig, line):
    from .game.controls import make_backend
    from .game.io import GameIO
    from .game.telemetry import TelemetryReceiver

    receiver = TelemetryReceiver(cfg.telemetry)
    try:
        controls = make_backend(cfg.controls)
    except Exception:
        receiver.close()
        raise
    return GameIO(receiver, controls, (float(line.xy[0, 0]), float(line.xy[0, 1])), cfg.controls, cfg.reset,
                  cfg.telemetry, log=log)


def cmd_check_telemetry(args) -> int:
    from .game.telemetry import TelemetryReceiver

    cfg = _config(args.config)
    rx = TelemetryReceiver(cfg.telemetry)
    log(f"listening for {cfg.telemetry.format} telemetry on {cfg.telemetry.host}:{cfg.telemetry.port} "
        "- drive around; Ctrl+C stops")
    prev = None
    end = time.monotonic() + args.seconds if args.seconds else math.inf
    try:
        while time.monotonic() < end:
            time.sleep(0.5)
            st, age = rx.latest()
            if st is None:
                log(f"  no packets yet (rejected: {rx.rejected})")
                continue
            motion = ""
            if prev is not None and math.hypot(st.x - prev.x, st.y - prev.y) > 1.0 and st.speed > 3:
                travel = math.atan2(st.y - prev.y, st.x - prev.x)
                diff = math.degrees((st.heading - travel + math.pi) % (2 * math.pi) - math.pi)
                motion = f"  heading-vs-travel {diff:+6.1f} deg"
            prev = st
            log(f"  packets {rx.packets:6d}  age {age * 1000:5.0f} ms  race_on {int(st.race_on)}  "
                f"x {st.x:8.1f}  y {st.y:8.1f}  heading {math.degrees(st.heading):7.1f}  "
                f"{st.speed * 3.6:6.1f} km/h  upright {st.upright:+.2f}{motion}")
    except KeyboardInterrupt:
        pass
    finally:
        rx.close()
    log("heading-vs-travel should stay near 0 while driving straight; if it is ~180 or drifts with "
        "direction, fix [telemetry] yaw_sign / yaw_offset_deg (forza format only)")
    return 0


def cmd_record(args) -> int:
    from .game.telemetry import TelemetryReceiver
    from .record import record_run, save_trace, states_to_trace

    cfg = _config(args.config)
    rx = TelemetryReceiver(cfg.telemetry)
    log(f"recording from {cfg.telemetry.host}:{cfg.telemetry.port}. Drive the stage start to finish, "
        f"then stop the car (recording ends after {args.idle_stop:.0f}s standing still, or Ctrl+C).")
    try:
        states = record_run(rx, idle_stop=args.idle_stop, log=log)
    finally:
        rx.close()
    if len(states) < 10:
        log("nothing recorded - check `aorbot check-telemetry`")
        return 1
    save_trace(args.out, states_to_trace(states))
    log(f"saved {len(states)} samples to {args.out}; next: aorbot build-line {args.out}")
    return 0


def cmd_build_line(args) -> int:
    from .record import build_line, load_trace

    line, grip = build_line(load_trace(args.trace), spacing=args.spacing, smooth=args.smooth, trim_end=args.trim_end)
    line.save(args.out)
    log(f"reference line: {line.length:.0f} m, {len(line.xy)} points -> {args.out}")
    log(f"demonstration: {line.meta['demo_time']:.2f}s, used up to ~{grip:.2f} m/s^2 sideways "
        f"(stored as the grip estimate; [speed] grip = 0 uses it)")
    return 0


def cmd_import_path(args) -> int:
    from .import_path import line_from_candidate, load_candidates, pick

    dump, cands = load_candidates(args.dump)
    log(f"scene '{dump['scene']}': {len(cands)} candidate path(s) found by the plugin")
    for c in cands:
        verdict = "ok" if c.ok else "; ".join(c.problems)
        log(f"  [{c.index:2d}] {c.length:7.0f} m ahead, {c.car_distance:5.1f} m from car, "
            f"{c.angle:3.0f} deg  {verdict:<28} {c.source}")
    chosen = cands[args.pick] if args.pick is not None else pick(cands)
    if chosen is None:
        log(f"no usable stage path found - send me {args.dump} and I'll target the right game class")
        return 1
    line = line_from_candidate(chosen, dump["scene"])
    line.save(args.out)
    log(f"using [{chosen.index}]: {line.length:.0f} m reference line -> {args.out}")
    log("no recorded run, so grip starts at the default and the learner finds the real limit")
    return 0


def _load_line_and_config(args):
    from .line import RacingLine
    from .pipeline import resolve_grip

    cfg = _config(args.config)
    line = RacingLine.load(args.line)
    return resolve_grip(cfg, line, log), line


def cmd_test_reset(args) -> int:
    from .game.io import ResetFailed

    cfg, line = _load_line_and_config(args)
    game = _open_game(cfg, line)
    try:
        t0 = time.monotonic()
        st = game.reset()
        log(f"reset OK in {time.monotonic() - t0:.1f}s: car at ({st.x:.1f}, {st.y:.1f}), "
            f"line start ({line.xy[0, 0]:.1f}, {line.xy[0, 1]:.1f})")
        return 0
    except ResetFailed as exc:
        log(f"reset failed: {exc}")
        return 1
    finally:
        game.close()


def cmd_learn(args) -> int:
    from .pipeline import learn, make_learner

    cfg, line = _load_line_and_config(args)
    learner = make_learner(line, cfg)
    state = Path(args.state)
    if state.exists():
        learner.load(state)
        log(f"resuming {state}: {learner.iteration} attempts so far, best "
            f"{learner.best['time']:.2f}s" if learner.best else f"resuming {state}: no finish yet")
    log(f"learning [{cfg.conditions.label()}] - keep the game window focused; Ctrl+C stops safely")
    traces = Path(args.save_traces) if args.save_traces else state.parent / "attempts"
    traces.mkdir(parents=True, exist_ok=True)

    def keep(result, rec):
        np.savez(traces / f"attempt_{rec['iteration']:04d}.npz", **result.trace)

    from .game.io import ResetFailed, TelemetryLost

    game = _open_game(cfg, line)
    try:
        learn(game, line, cfg, learner, args.iterations, stop_after_finishes=args.stop_after_finishes,
              save_path=state, log=log, on_episode=keep)
    except KeyboardInterrupt:
        log("stopped; progress so far is saved")
    except (ResetFailed, TelemetryLost) as exc:
        log(f"stopped: {exc}; progress so far is saved")
    finally:
        game.close()
    if learner.best:
        log(f"best: {learner.best['time']:.2f}s (attempt {learner.best['iteration']}); state in {state}")
    return 0


def cmd_drive(args) -> int:
    from .pipeline import drive_best, make_learner

    cfg, line = _load_line_and_config(args)
    learner = make_learner(line, cfg)
    learner.load(args.state)
    if learner.best is None:
        log("no finished attempt in this state yet; driving with the current (still learning) profile")
    game = _open_game(cfg, line)
    try:
        result = drive_best(game, line, cfg, learner)
    finally:
        game.close()
    log(f"{result.status.value}: {result.time:.2f}s, reached {result.progress:.0f}/{line.length:.0f} m")
    return 0 if result.finished else 1


def cmd_show(args) -> int:
    from .pipeline import format_record

    data = json.loads(Path(args.state).read_text())
    c = data["conditions"]
    log(f"{c['stage']} | {c['car']} | {c['weather']}: {data['iteration']} attempts")
    for rec in data["history"][-args.last:]:
        log(format_record(rec))
    if data["best"]:
        log(f"best {data['best']['time']:.2f}s at attempt {data['best']['iteration']}")
    return 0


def _print_verdict(verdict: dict) -> None:
    for name, ok in verdict["checks"].items():
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    log(f"  summary: {json.dumps(verdict['summary'])}")
    log(f"VERDICT: {'PASS - the bot learned this stage' if verdict['passed'] else 'NOT YET'}")


def cmd_report(args) -> int:
    from .episode import EpisodeResult, Status
    from .pipeline import make_learner
    from .report import learning_verdict

    cfg, line = _load_line_and_config(args)
    learner = make_learner(line, cfg)
    learner.load(args.state)
    verdict = learning_verdict(learner.history, stable_window=args.stable)
    if "demo_time" in line.meta:
        verdict["summary"]["your_demo_time"] = round(line.meta["demo_time"], 3)
    log(f"[{cfg.conditions.label()}]")
    _print_verdict(verdict)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"conditions": cfg.conditions.label(), **verdict}, indent=1))
    traces = Path(args.traces) if args.traces else Path(args.state).parent / "attempts"
    results = []
    for rec in learner.history:
        f = traces / f"attempt_{rec['iteration']:04d}.npz"
        trace = {}
        if f.exists():
            with np.load(f) as data:
                trace = {k: data[k] for k in data.files}
        results.append(EpisodeResult(Status(rec["status"]), rec["time"], rec["progress"], rec["end_s"], trace))
    try:
        from .plot import plot_learning

        plot_learning(out, line, learner, results, title=f"{cfg.conditions.label()} | {len(results)} attempts")
        log(f"wrote {out} and {out.with_suffix('.json')}")
    except ImportError:
        log(f"wrote {out.with_suffix('.json')} (pip install matplotlib for the chart)")
    return 0 if verdict["passed"] else 1


# ------------------------------------------------------------ optional RL
def _sim_parts(args):
    from .pipeline import resolve_grip, sim_config, simulate_demo
    from .record import build_line
    from .sim.stage import make_stage

    stage = make_stage(args.seed, length=args.length)
    cfg = sim_config(stage, args.weather)
    line, _ = build_line(simulate_demo(stage, args.weather, cfg, seed=args.seed))
    return stage, line, resolve_grip(cfg, line, log)


def cmd_rl_train(args) -> int:
    from .rl import RallyEnv, evaluate, train_ppo
    from .sim.io import SimIO

    stage, line, cfg = _sim_parts(args)
    env = RallyEnv(SimIO(stage, args.weather), line, cfg)
    log(f"baseline (line follower, no residual): {evaluate(env)}")
    log(f"training PPO for {args.steps} steps on sim stage {args.seed} ({line.length:.0f} m)")
    model = train_ppo(env, args.steps, args.out, log)
    log(f"trained: {evaluate(env, model)}; saved {args.out}")
    return 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aorbot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sim-demo", help="whole pipeline offline on a simulated stage")
    s.add_argument("--seed", type=int, default=3, help="which procedural stage")
    s.add_argument("--weather", default="dry", help="dry | wet | snow")
    s.add_argument("--length", type=float, default=2000.0)
    s.add_argument("--iterations", type=int, default=40)
    s.add_argument("--init-scale", type=float, default=1.3,
                   help="first attempt speed vs the demo (>1 starts too fast on purpose to show crash learning)")
    s.add_argument("--config", help="optional TOML to override controller/learn settings")
    s.add_argument("--out", default="runs/sim-demo")
    s.add_argument("--no-plot", action="store_true")
    s.set_defaults(func=cmd_sim_demo)

    s = sub.add_parser("fake-game", help="simulator behind the game's UDP protocols (test without the game)")
    s.add_argument("--config", help="use its [telemetry] and [controls] udp ports/format")
    s.add_argument("--seed", type=int, default=3)
    s.add_argument("--weather", default="dry")
    s.add_argument("--length", type=float, default=2000.0)
    s.add_argument("--time-scale", type=float, default=1.0)
    s.add_argument("--write-line", help="also write a reference line for this stage to this .npz")
    s.set_defaults(func=cmd_fake_game)

    s = sub.add_parser("check-telemetry", help="print live telemetry to verify the mod")
    s.add_argument("--config")
    s.add_argument("--seconds", type=float, default=0.0, help="stop after this long (0 = until Ctrl+C)")
    s.set_defaults(func=cmd_check_telemetry)

    s = sub.add_parser("record", help="record your own run through the stage")
    s.add_argument("--config")
    s.add_argument("--out", default="runs/demo_trace.npz")
    s.add_argument("--idle-stop", type=float, default=5.0)
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("build-line", help="turn a recorded run into the reference line")
    s.add_argument("trace")
    s.add_argument("--out", default="runs/line.npz")
    s.add_argument("--spacing", type=float, default=2.0)
    s.add_argument("--smooth", type=float, default=8.0, help="smoothing window in metres")
    s.add_argument("--trim-end", type=float, default=0.0, help="metres to cut off the end (past the finish)")
    s.set_defaults(func=cmd_build_line)

    s = sub.add_parser("import-path", help="reference line from the game's own stage path (no recorded run)")
    s.add_argument("--dump", default=str(Path("..") / "BepInEx" / "aorbot" / "paths_latest.json"),
                   help="written by the plugin when you load a stage (or press F9)")
    s.add_argument("--out", default="runs/line.npz")
    s.add_argument("--pick", type=int, help="use this candidate number instead of the automatic choice")
    s.set_defaults(func=cmd_import_path)

    for name, func, text in (
        ("test-reset", cmd_test_reset, "run the restart macro once and check the car is at the start"),
        ("learn", cmd_learn, "repeat the stage and learn (resumes from --state)"),
        ("drive", cmd_drive, "one run with the best learned profile"),
    ):
        s = sub.add_parser(name, help=text)
        s.add_argument("--config", required=True)
        s.add_argument("--line", default="runs/line.npz")
        if name != "test-reset":
            s.add_argument("--state", default="runs/learner.json")
        if name == "learn":
            s.add_argument("--iterations", type=int, default=30)
            s.add_argument("--stop-after-finishes", type=int, default=0,
                           help="stop after this many finishes in a row (0 = run all iterations)")
            s.add_argument("--save-traces", help="where to keep every attempt's trace (default: attempts/ next to --state)")
        s.set_defaults(func=func)

    s = sub.add_parser("rl-train", help="optional: train a residual PPO policy in the simulator")
    s.add_argument("--seed", type=int, default=3)
    s.add_argument("--weather", default="dry")
    s.add_argument("--length", type=float, default=2000.0)
    s.add_argument("--steps", type=int, default=200_000)
    s.add_argument("--out", default="runs/ppo_residual.zip")
    s.set_defaults(func=cmd_rl_train)

    s = sub.add_parser("report", help="verdict + chart: did the bot learn the stage?")
    s.add_argument("--config", required=True)
    s.add_argument("--line", default="runs/line.npz")
    s.add_argument("--state", default="runs/learner.json")
    s.add_argument("--traces", help="attempt traces (default: attempts/ next to --state)")
    s.add_argument("--stable", type=int, default=5, help="how many final attempts must all finish")
    s.add_argument("--out", default="runs/report.png")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("show", help="summarise a learner state file")
    s.add_argument("--state", default="runs/learner.json")
    s.add_argument("--last", type=int, default=20)
    s.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

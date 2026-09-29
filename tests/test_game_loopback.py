"""The real-game code path (UDP telemetry receiver, UDP controls, restart
macro, real-time pacing) against the simulator behind the same protocols."""

import dataclasses

import pytest

from aorbot.config import ControlsConfig, ResetConfig, TelemetryConfig
from aorbot.game.controls import make_backend
from aorbot.game.io import GameIO, ResetFailed
from aorbot.game.telemetry import TelemetryReceiver
from aorbot.pipeline import learn, make_learner, resolve_grip, sim_config, simulate_demo
from aorbot.record import build_line
from aorbot.sim.io import FakeGame
from aorbot.sim.stage import make_stage


@pytest.mark.parametrize("fmt", ["aorbot", "forza"])
def test_learning_through_udp_like_the_real_game(fmt, free_ports):
    tele_port, ctrl_port = free_ports
    stage = make_stage(5, length=300)
    cfg = sim_config(stage, "dry")
    line, _ = build_line(simulate_demo(stage, "dry", cfg))
    cfg = resolve_grip(cfg, line, log=lambda m: None)
    cfg = dataclasses.replace(
        cfg,
        telemetry=TelemetryConfig(format=fmt, port=tele_port),
        controls=ControlsConfig(backend="udp", udp_port=ctrl_port, control_hz=60),
        reset=ResetConfig(macro=["RESTART"], countdown=0.1, settle_time=0.2, timeout=10),
    )
    game = FakeGame(stage, "dry", telemetry_addr=("127.0.0.1", tele_port),
                    control_addr=("127.0.0.1", ctrl_port), fmt=fmt, time_scale=4.0).start()
    io = GameIO(TelemetryReceiver(cfg.telemetry), make_backend(cfg.controls), tuple(line.xy[0]),
                cfg.controls, cfg.reset, cfg.telemetry)
    try:
        learner = make_learner(line, cfg)
        results = learn(io, line, cfg, learner, 2, log=lambda m: None)
    finally:
        io.close()
        game.stop()
    assert game.restarts == 2
    assert all(r.finished for r in results)
    assert learner.best is not None


def test_report_on_a_learned_state(tmp_path, capsys):
    """sim-demo writes state + line; report turns them into a verdict."""
    from aorbot.cli import main

    out = tmp_path / "demo"
    main(["sim-demo", "--seed", "5", "--length", "500", "--iterations", "12", "--init-scale", "1.4",
          "--out", str(out), "--no-plot"])
    cfg = tmp_path / "c.toml"
    cfg.write_text('[conditions]\nstage = "sim-5"\ncar = "sim-car"\nweather = "dry"\n'
                   '[rules]\nmax_offset = 6.2\n')
    code = main(["report", "--config", str(cfg), "--line", str(out / "line.npz"),
                 "--state", str(out / "learner.json"), "--out", str(tmp_path / "report.png"), "--stable", "3"])
    text = capsys.readouterr().out
    assert "VERDICT" in text and (tmp_path / "report.json").exists()
    assert code == 0, text


def test_reset_fails_cleanly_without_the_game(free_ports):
    tele_port, ctrl_port = free_ports
    cfg_t = TelemetryConfig(port=tele_port)
    cfg_c = ControlsConfig(backend="udp", udp_port=ctrl_port)
    io = GameIO(TelemetryReceiver(cfg_t), make_backend(cfg_c), (0.0, 0.0), cfg_c,
                ResetConfig(macro=["RESTART", "wait 0.05"], timeout=0.6), cfg_t)
    try:
        with pytest.raises(ResetFailed, match="no telemetry"):
            io.reset()
    finally:
        io.close()

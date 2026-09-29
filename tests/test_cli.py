import json

from aorbot.cli import main
from aorbot.line import RacingLine


def test_sim_demo_and_show(tmp_path, capsys):
    out = tmp_path / "demo"
    code = main(["sim-demo", "--seed", "5", "--length", "500", "--iterations", "6",
                 "--init-scale", "0.8", "--out", str(out), "--no-plot"])
    assert code == 0
    assert (out / "demo_trace.npz").exists()
    line = RacingLine.load(out / "line.npz")
    assert line.meta["grip_estimate"] > 0
    state = json.loads((out / "learner.json").read_text())
    assert state["iteration"] == 6 and state["best"] is not None

    assert main(["show", "--state", str(out / "learner.json"), "--last", "2"]) == 0
    text = capsys.readouterr().out
    assert "finished" in text and "best" in text


def test_build_line_from_recorded_trace(tmp_path):
    out = tmp_path / "demo"
    main(["sim-demo", "--seed", "5", "--length", "400", "--iterations", "1", "--out", str(out), "--no-plot"])
    assert main(["build-line", str(out / "demo_trace.npz"), "--out", str(tmp_path / "line.npz")]) == 0
    assert RacingLine.load(tmp_path / "line.npz").length > 350

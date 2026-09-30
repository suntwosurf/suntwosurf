from zadbot.params import defaults
from zadbot.report import learning_report


def test_learning_report_running_average():
    hist = [{"generation": g, "centre_fitness": f, "best_fitness": 0.9}
            for g, f in enumerate([-1.0, 1.0, 0.0, 0.0, 0.0, 1.0])]
    lines = learning_report({"game_version": "0.28.0", "generation": 6, "history": hist, "centre": defaults()})
    rows = [line.split() for line in lines if line[:3].strip().isdigit()]
    assert [r[2] for r in rows] == ["-1.00", "+0.00", "+0.00", "+0.00", "+0.00", "+0.40"]
    assert any("avg5" in line for line in lines)

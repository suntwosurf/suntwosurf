from zadbot.params import defaults
from zadbot.report import learning_report


def test_learning_report_running_average():
    hist = [{"generation": g, "centre_fitness": f, "best_fitness": 0.9}
            for g, f in enumerate([-1.0, 1.0, 0.0, 0.0, 0.0, 1.0])]
    lines = learning_report({"game_version": "0.28.0", "generation": 6, "history": hist, "centre": defaults()})
    rows = [line.split() for line in lines if line[:3].strip().isdigit()]
    assert [r[2] for r in rows] == ["-1.00", "+0.00", "+0.00", "+0.00", "+0.00", "+0.40"]
    assert any("avg5" in line for line in lines)


def test_paired_comparison_matches_hand_count():
    """The first real evaluation: 14 maps only learned won, 6 only petra won."""
    from zadbot.pipeline import paired_comparison
    from zadbot.report import evaluation_report

    learned = [1.0] * 15 + [1.0] * 14 + [-1.0] * 6 + [-1.0] * 13
    petra = [1.0] * 15 + [-0.5] * 14 + [0.5] * 6 + [-1.0] * 13
    pc = paired_comparison(learned, petra)
    assert (pc["better"], pc["worse"]) == (14, 6)
    assert abs(pc["p"] - 0.1153) < 0.001
    assert paired_comparison([1.0, None], [-1.0, 1.0]) == {"better": 1, "worse": 0, "p": 1.0}
    assert paired_comparison([], [])["p"] == 1.0

    row = {"wins": 29, "losses": 19, "draws": 0, "conquest_wins": 12, "conquest_losses": 10, "win_rate": 29 / 48,
           "mean_outcome": 0.1, "mean_score_margin": 0.08, "mean_game_minutes": 26.9, "unfinished": 0}
    lines = evaluation_report({"learned": dict(row, paired=pc), "petra": row})
    text = "\n".join(lines)
    assert "learned won 14 that petra lost, petra won 6 that learned lost" in text
    assert "about 1 in 9" in text and "not proven yet" in text

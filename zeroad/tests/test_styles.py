import pytest

from zadbot.params import BY_NAME, clamp
from zadbot.pipeline import style_analysis
from zadbot.report import style_report
from zadbot.styles import STYLES, parse_settings, style_values


@pytest.mark.parametrize("name", list(STYLES))
def test_styles_are_inside_the_ranges(name):
    values = STYLES[name]["values"]
    assert set(values) <= set(BY_NAME)
    assert clamp(values) == style_values(name) == dict(clamp({}), **values)


def test_parse_settings():
    assert parse_settings(" rush, boom,rush,learned") == ["rush", "boom", "learned"]
    with pytest.raises(ValueError, match="unknown setting.*nope.*choose from rush"):
        parse_settings("rush,nope")
    with pytest.raises(ValueError):
        parse_settings(" , ")


def _results(outcomes, paired=None):
    return {name: {"wins": sum(o > 0 for o in outs), "losses": sum(o <= 0 for o in outs), "draws": 0,
                   "conquest_wins": 0, "conquest_losses": 0, "win_rate": sum(o > 0 for o in outs) / len(outs),
                   "mean_outcome": 0.0, "mean_score_margin": 0.0, "mean_game_minutes": 30.0, "unfinished": 0,
                   **({"paired": paired[name]} if paired and name in paired else {})}
            for name, outs in outcomes.items()}


def test_style_per_civ_found_on_held_out_maps():
    """rush always wins as athen and loses as brit, boom the other way round."""
    groups = ["athen" if i % 2 == 0 else "brit" for i in range(40)]
    outcomes = {
        "petra": [1.0 if i % 3 == 0 else -1.0 for i in range(40)],
        "rush": [1.0 if g == "athen" else -1.0 for g in groups],
        "boom": [-1.0 if g == "athen" else 1.0 for g in groups],
    }
    a = style_analysis(outcomes, groups)
    assert a["per_group_win_rate"] == 1.0 and a["one_for_all_win_rate"] == 0.5
    assert all(c["per_group"] == {"athen": "rush", "brit": "boom"} for c in a["choices"])
    assert (a["selector"]["better"], a["selector"]["worse"]) == (20, 0)
    assert a["by_group"]["athen"]["rush"] == [20, 20] and a["by_group"]["brit"]["rush"] == [0, 20]
    text = "\n".join(style_report(a, _results(outcomes)))
    assert "a style per civ wins 100 %, one style for all civs 50 %" in text
    assert "strategy selector is worth building" in text
    assert "athen" in text and "20/20" in text


def test_no_gain_without_differences():
    groups = ["athen", "brit", "gaul"] * 20
    same = [1.0 if i % 5 < 2 else -1.0 for i in range(60)]
    outcomes = {"petra": list(same), "rush": list(same), "turtle": list(same)}
    a = style_analysis(outcomes, groups)
    assert a["selector"]["p"] == 1.0 and a["matches_per_group"] == 20
    assert all(c["overall"] == "petra" and set(c["per_group"].values()) == {"petra"} for c in a["choices"])
    none = {"better": 0, "worse": 0, "p": 1.0, "matches": 60, "diff": 0.0, "range": 0.0}
    text = "\n".join(style_report(a, _results(outcomes, {"rush": none, "turtle": none})))
    assert "No: no style differs clearly from petra" in text
    assert "not shown: about 10 maps per civ to choose from is too few" in text


def test_unfinished_matches_are_left_out():
    groups = ["athen"] * 8
    outcomes = {"petra": [None, -1.0, -1.0, 1.0, None, None, -1.0, 1.0], "rush": [1.0] * 8}
    a = style_analysis(outcomes, groups)
    assert a["by_group"]["athen"]["petra"] == [2, 5]
    assert a["selector"]["matches"] == 8 and a["per_group_win_rate"] == 1.0

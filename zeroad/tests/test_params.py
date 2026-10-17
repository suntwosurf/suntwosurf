import math

import pytest

from zadbot import params
from zadbot.params import BY_NAME, PARAMS


def test_defaults_are_plain_petra():
    for p in PARAMS:
        assert p.lo <= p.default <= p.hi
        if p.mode == "scale":
            assert p.default == 1.0  # x1 = Petra's own value


def test_unit_round_trip():
    for p in PARAMS:
        for v in (p.lo, p.default, p.hi, (p.lo + p.hi) / 2):
            assert params.from_unit(p, params.to_unit(p, v)) == pytest.approx(v)
    assert params.unit_to_values(params.values_to_unit(params.defaults())) == pytest.approx(params.defaults())


def test_log_params_centre_default():
    # log-scaled multipliers: 1.0 sits where the range is symmetric in log space
    p = BY_NAME["support_ratio"]  # 0.5 .. 2.0
    assert params.to_unit(p, 1.0) == pytest.approx(0.5)


def test_clamp_fills_defaults_and_limits():
    v = params.clamp({"aggressive": 7.0, "workers": 0.01})
    assert v["aggressive"] == 1.0
    assert v["workers"] == BY_NAME["workers"].lo
    assert v["defensive"] == 0.5
    with pytest.raises(KeyError):
        params.clamp({"nope": 1})


def test_params_js_round_trip():
    v = params.defaults()
    v["phase2_pop"] = 0.7
    text = params.params_js(v, comment="test")
    assert text.startswith("// Written by zadbot")
    entries = params.parse_params_js(text)
    assert entries["Economy.popPhase2"] == {"mode": "scale", "value": 0.7, "round": True, "min": 10}
    assert entries["personality.aggressive"] == {"mode": "set", "value": 0.5}
    assert set(entries) == {p.target for p in PARAMS}
    assert params.parse_params_js(params.params_js(None)) == {}


def test_describe_mentions_every_setting():
    lines = params.describe(params.defaults())
    assert len(lines) == len(PARAMS)
    assert all("same as Petra" in line or "(Petra" in line for line in lines)
    assert not any(math.isnan(p.default) for p in PARAMS)

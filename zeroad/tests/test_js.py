"""The mod's JavaScript, run with Node.js.

* ZadbotReport.js in a mocked simulation (always, if node is installed).
* learned.js on top of the real Petra of the installed game: every learned
  setting must exist in that Petra and be applied as intended, and a plain
  Petra in the same match must stay unchanged. Needs node and the game
  (ZADBOT_GAME or ZADBOT_PETRA_ROOT, see conftest.py); skipped otherwise.
"""

import json
import math
import os
import shutil
import subprocess

import pytest

from conftest import JS_DIR, MOD_ROOT
from zadbot.params import BY_NAME, PARAMS, defaults, learned_entries
from zadbot.styles import STYLES, style_values

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")


def test_report_script():
    r = subprocess.run([NODE, str(JS_DIR / "check_report.mjs"), str(MOD_ROOT / "maps/scripts/ZadbotReport.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def run_check(petra_root, values, difficulty):
    env = dict(os.environ, ZADBOT_JS_ROOTS=os.pathsep.join([str(MOD_ROOT), str(petra_root)]),
               ZADBOT_TEST_ENTRIES=json.dumps(learned_entries(values)))
    r = subprocess.run([NODE, "--import", "./register.mjs", "check_learned.mjs", str(difficulty)],
                       cwd=JS_DIR, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("difficulty", [1, 3, 5])
def test_learned_settings_on_real_petra(petra_root, difficulty):
    if petra_root is None:
        pytest.skip("needs the game's Petra: set ZADBOT_GAME or ZADBOT_PETRA_ROOT")
    values = dict(defaults(), aggressive=0.9, defensive=0.1, phase2_pop=0.5, workers=1.3,
                  barracks1_pop=0.6, support_ratio=1.5, soldier_priority=1.4)
    out = run_check(petra_root, values, difficulty)

    assert out["warnings"] == [], "a learned setting does not exist in this Petra version"
    assert out["prototypeUntouched"] and out["botIsPetra"] and out["configIsPetraConfig"]
    assert out["plainAfter"] == out["plain"], "the learned bot changed plain Petra"
    assert out["defaultBotLearned"] == {}  # the shipped zadbot AI is plain Petra until install-mod
    assert out["restoredLearned"] == learned_entries(values)  # survives save/load
    assert out["personalitySaved"] and out["personalityIsData"]

    # Petra's own settings that follow the personality follow the learned one
    plain, learned, ref = out["plain"], out["learned"], out["reference"]
    for key in ["Military.numSentryTowers", "Military.towerLapseTime", "priorities.defenseBuilding"]:
        assert learned[key] == ref[key], key
    assert learned["Military.towerLapseTime"] == round(360 * (1.1 - 0.2 * 0.1))  # defensive 0.1
    assert learned["personality.cooperative"] == plain["personality.cooperative"]  # not learned: drawn as usual
    if difficulty >= 3:  # aggressive 0.9: Petra's early barracks and town phase, then the learned multipliers
        assert (ref["Economy.popPhase2"], ref["Military.popForBarracks1"]) == (50, 12)
        assert (learned["Economy.popPhase2"], learned["Military.popForBarracks1"]) == (25, 7)
        assert learned["Military.numSentryTowers"] == plain["Military.numSentryTowers"] - 1  # defensive 0.1

    for p in PARAMS:
        v = values[p.name]
        if p.mode == "set":
            assert learned[p.target] == pytest.approx(v)
            continue
        if ref[p.target] == "Infinity":
            continue
        want = ref[p.target] * v
        if p.round:
            want = math.floor(want + 0.5)  # JavaScript's Math.round
        want = max(p.min_value or -1e9, want)
        if p.target == "Economy.workPhase3":  # Petra's rule: never above the worker target
            want = min(want, learned["Economy.targetNumWorkers"])
        assert learned[p.target] == pytest.approx(want, abs=1e-9), p.name


def test_default_values_keep_petra(petra_root):
    if petra_root is None:
        pytest.skip("needs the game's Petra: set ZADBOT_GAME or ZADBOT_PETRA_ROOT")
    out = run_check(petra_root, defaults(), 3)
    for p in PARAMS:
        if p.mode == "scale":
            assert out["learned"][p.target] == out["plain"][p.target], p.name
    assert out["learned"][BY_NAME["aggressive"].target] == 0.5


@pytest.mark.parametrize("style", list(STYLES))
def test_styles_on_real_petra(petra_root, style):
    if petra_root is None:
        pytest.skip("needs the game's Petra: set ZADBOT_GAME or ZADBOT_PETRA_ROOT")
    out = run_check(petra_root, style_values(style), 3)
    assert out["warnings"] == [] and out["plainAfter"] == out["plain"]
    plain, learned = out["plain"], out["learned"]
    if style == "rush":  # Petra's own rush: early barracks and town phase
        assert learned["Military.popForBarracks1"] < plain["Military.popForBarracks1"]
        assert learned["Economy.popPhase2"] < plain["Economy.popPhase2"]
    if style == "turtle":  # more sentry towers, sooner towers, more for defence buildings
        assert learned["Military.numSentryTowers"] > plain["Military.numSentryTowers"]
        assert learned["Military.towerLapseTime"] < plain["Military.towerLapseTime"]
        assert learned["priorities.defenseBuilding"] > plain["priorities.defenseBuilding"]
    if style == "boom":
        assert learned["Economy.targetNumWorkers"] > plain["Economy.targetNumWorkers"]
        assert learned["Military.popForBarracks1"] > plain["Military.popForBarracks1"]

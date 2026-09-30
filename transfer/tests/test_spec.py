"""The 0 A.D. recorder (JS) and this package must use the same names."""

import re
from pathlib import Path

from rtsconcepts import spec

RTS_JS = Path(__file__).parents[2] / "zeroad" / "zadbot" / "mod" / "zadbot" / "simulation" / "ai" / "zadbot" / "rts.js"


def js_list(text: str, name: str) -> list[str]:
    body = re.search(rf"export const {name} = \[(.*?)\];", text, re.S).group(1)
    return re.findall(r'"([A-Za-z_0-9]+)"', re.sub(r"//[^\n]*", "", body))


def test_recorder_names_match():
    text = RTS_JS.read_text(encoding="utf-8")
    assert js_list(text, "FEATURES") == spec.FEATURES
    assert js_list(text, "PRIVILEGED") == spec.PRIVILEGED
    assert js_list(text, "ACTIONS") == spec.ACTIONS
    assert int(re.search(r"export const SPEC_VERSION = (\d+);", text).group(1)) == spec.SPEC_VERSION
    assert int(re.search(r"export const STEP_SECONDS = (\d+);", text).group(1)) == spec.STEP_SECONDS
    assert len(set(spec.FEATURES)) == len(spec.FEATURES)

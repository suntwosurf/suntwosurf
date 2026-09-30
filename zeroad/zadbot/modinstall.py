"""Install the zadbot mod into the game's user mods folder.

The mod (zadbot/mod/zadbot/) holds:
  * the ``zadbot`` AI: Petra from the installed game + learned settings
    (params.js). It shows up in the game setup like Petra, so you can also
    play against it yourself.
  * training "slot" AIs ``zadbot_t0``, ``zadbot_t1``...: the same bot, one per
    parallel match, each with the settings being tried in that match.
  * victory conditions ``zadbot_report`` and ``zadbot_limit_<N>`` that print
    match statistics for the runner (maps/scripts/ZadbotReport.js).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .params import params_js

MOD_NAME = "zadbot"
AI_NAME = "zadbot"
SLOT_PREFIX = "zadbot_t"


def mod_source() -> Path:
    return Path(__file__).parent / "mod" / MOD_NAME


def mod_dir(user_data: Path) -> Path:
    return user_data / "mods" / MOD_NAME


def mod_dependency() -> str:
    with open(mod_source() / "mod.json", encoding="utf-8") as fh:
        deps = json.load(fh)["dependencies"]
    return deps[0]


def install_mod(user_data: Path, learned: dict[str, float] | None = None, comment: str = "") -> Path:
    """Copy the mod and write the ``zadbot`` AI's settings (None = plain Petra)."""
    dest = mod_dir(user_data)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mod_source(), dest, dirs_exist_ok=True)
    (dest / "simulation" / "ai" / AI_NAME / "params.js").write_text(params_js(learned, comment), encoding="utf-8")
    return dest


def ensure_installed(user_data: Path) -> Path:
    """Install if missing or outdated, keeping the learned settings already there."""
    dest = mod_dir(user_data)
    params = dest / "simulation" / "ai" / AI_NAME / "params.js"
    kept = params.read_text(encoding="utf-8") if params.is_file() else None
    shutil.copytree(mod_source(), dest, dirs_exist_ok=True)
    if kept is not None:
        params.write_text(kept, encoding="utf-8")
    return dest


def victory_condition_for_limit(minutes: int) -> str:
    return f"zadbot_limit_{minutes}"


def ensure_time_limit(user_data: Path, minutes: int) -> str:
    """Write the victory condition file for a time limit; returns its name.

    The script reads the minutes back from the name (ZadbotReport.js)."""
    name = victory_condition_for_limit(minutes)
    path = mod_dir(user_data) / "simulation" / "data" / "settings" / "victory_conditions" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "TranslatedKeys": ["Title", "Description"],
        "Data": {
            "Title": f"zadbot: {minutes} min limit",
            "Description": f"After {minutes} minutes the player with the highest score wins.",
            "Scripts": ["scripts/ZadbotReport.js"],
            "GUIOrder": 101,
        },
    }
    text = json.dumps(data, indent="\t") + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return name


def slot_name(index: int) -> str:
    return f"{SLOT_PREFIX}{index}"


def write_slot(user_data: Path, index: int, values: dict[str, float] | None, comment: str = "") -> str:
    """Create/refresh training AI ``zadbot_t<index>`` with ``values``; returns its AI name."""
    name = slot_name(index)
    d = mod_dir(user_data) / "simulation" / "ai" / name
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "name": f"zadbot training slot {index}",
        "description": "Used by zadbot while learning. Pick 'zadbot (learned Petra)' instead.",
        "constructor": "ZadBot",
        "filename": "_zadbot.js",
        "useShared": True,
    }
    (d / "data.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (d / "_zadbot.js").write_text(
        'import { makeLearnedBot } from "simulation/ai/zadbot/learned.js";\n'
        f'import {{ LEARNED }} from "simulation/ai/{name}/params.js";\n\n'
        "export const ZadBot = makeLearnedBot(LEARNED);\n",
        encoding="utf-8",
    )
    (d / "params.js").write_text(params_js(values, comment), encoding="utf-8")
    return name


def remove_slots(user_data: Path) -> int:
    """Delete the training slot AIs (they clutter the AI list in the game setup)."""
    root = mod_dir(user_data) / "simulation" / "ai"
    n = 0
    for d in root.glob(f"{SLOT_PREFIX}*"):
        if d.is_dir():
            shutil.rmtree(d)
            n += 1
    return n


def installed_params(user_data: Path, ai: str = AI_NAME) -> str | None:
    path = mod_dir(user_data) / "simulation" / "ai" / ai / "params.js"
    return path.read_text(encoding="utf-8") if path.is_file() else None

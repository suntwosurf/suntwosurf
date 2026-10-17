"""Named playing styles: fixed settings far apart from each other.

``zadbot evaluate --settings rush,boom,turtle`` plays each style and plain
Petra on the same maps. That shows whether the style changes who wins at all,
and whether different civilisations want different styles, i.e. whether a
strategy selector on top of Petra would have something to learn.

Values are in the units of params.py. With aggressive above 0.7 Petra itself
builds its first barracks at 12 population, advances to the town phase at 50
and plans early rushes; with defensive above 0.7 it builds more towers and
fortresses. The multipliers act on top of that.
"""

from __future__ import annotations

from .params import clamp

STYLES: dict[str, dict] = {
    "rush": {
        "about": "attack early and often: early barracks, many soldiers, few women",
        "values": {"aggressive": 0.95, "defensive": 0.05, "workers": 0.8, "support_ratio": 0.6,
                   "barracks2_pop": 0.6, "soldier_priority": 1.6, "military_building_priority": 1.5},
    },
    "boom": {
        "about": "economy and fast phases first, army late",
        "values": {"aggressive": 0.05, "defensive": 0.3, "phase2_pop": 0.7, "phase3_workers": 0.8,
                   "workers": 1.5, "support_ratio": 1.6, "barracks1_pop": 1.6, "barracks2_pop": 1.6,
                   "soldier_priority": 0.6, "military_building_priority": 0.6},
    },
    "turtle": {
        "about": "defend: towers, fortresses, a solid army at home, few attacks",
        "values": {"aggressive": 0.05, "defensive": 0.95, "phase2_pop": 0.8, "phase3_workers": 0.8,
                   "workers": 1.2, "soldier_priority": 1.2, "military_building_priority": 1.3},
    },
}


def style_values(name: str) -> dict[str, float]:
    return clamp(STYLES[name]["values"])


def parse_settings(text: str) -> list[str]:
    """``"rush, boom,learned"`` -> ["rush", "boom", "learned"]; unknown names raise ValueError."""
    names = [n.strip() for n in text.split(",") if n.strip()]
    known = list(STYLES) + ["learned", "petra"]
    bad = [n for n in names if n not in known]
    if bad or not names:
        raise ValueError(f"unknown setting(s) {', '.join(bad) or '(none)'}: choose from {', '.join(known)}")
    return list(dict.fromkeys(names))

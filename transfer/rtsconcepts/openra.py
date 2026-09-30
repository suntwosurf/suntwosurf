"""OpenRA (Red Alert) side of the shared view (transfer/DESIGN.md).

* ``OpenRAEncoder``: the 21 shared features (spec.FEATURES) from the state
  OpenRA-RL's ``get_game_state`` tool returns, in the same units as the
  0 A.D. recorder (money / 100, per minute, the last minute).
* ``OpenRAExecutor``: carries out the shared macro actions with OpenRA-RL's
  tools (``build_unit``, ``build_and_place``, ``attack_move``, ...).

Nothing here needs OpenRA-RL installed; ``play`` (openra_play.py) does.
"""

from __future__ import annotations

import math
from collections import deque

from .spec import ACTIONS, FEATURES, STEP_SECONDS

TICKS_PER_SECOND = 25  # OpenRA's default game speed: 40 ms per tick
STEP_TICKS = STEP_SECONDS * TICKS_PER_SECOND
WINDOW_STEPS = 6  # "the last minute"
HOME_CELLS = 15  # 0 A.D. uses 60 m = 15 tiles around own structures

# Red Alert costs (mods/ra rules, via OpenRA-RL's game_data)
COST = {
    "e1": 100, "e2": 150, "e3": 300, "e4": 300, "e6": 400, "e7": 1800, "medi": 200, "mech": 500, "spy": 500,
    "thf": 500, "shok": 350, "dog": 200, "1tnk": 700, "2tnk": 850, "3tnk": 1150, "4tnk": 2000, "v2rl": 900,
    "jeep": 500, "apc": 850, "arty": 850, "harv": 1100, "mcv": 2000, "ftrk": 600, "mnly": 800, "ttnk": 1350,
    "ctnk": 1350, "stnk": 1000, "qtnk": 2000, "dtrk": 2500, "mgg": 1000, "mrj": 1000, "truk": 500, "heli": 2000,
    "hind": 1500, "mh60": 1500, "tran": 900, "yak": 1350, "mig": 2000, "ss": 950, "dd": 1000, "ca": 2400,
    "pt": 500, "lst": 500, "msub": 2000,
    "fact": 2000, "powr": 300, "apwr": 500, "barr": 500, "tent": 500, "proc": 1400, "weap": 2000, "dome": 1500,
    "fix": 1200, "atek": 1500, "stek": 1500, "hpad": 500, "afld": 500, "spen": 800, "syrd": 1000, "silo": 150,
    "kenn": 200, "pbox": 600, "hbox": 750, "gun": 800, "ftur": 600, "tsla": 1200, "agun": 800, "sam": 700,
    "gap": 800, "iron": 2000, "pdox": 1500, "mslo": 2500,
}
WORKERS = {"harv"}
NOT_ARMY = {"harv", "mcv", "truk", "e6", "medi", "mech", "spy", "thf", "mnly", "mgg", "mrj", "tran", "lst"}
ECONOMY = {"proc", "silo"}
PRODUCTION = {"tent", "barr", "weap", "afld", "hpad", "spen", "syrd", "kenn"}
BASES = {"fact"}
DEFENSES = {"pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam"}
TECH_MID, TECH_HIGH = {"dome"}, {"atek", "stek"}


COST_BUILDINGS = {
    "fact", "powr", "apwr", "barr", "tent", "proc", "weap", "dome", "fix", "atek", "stek", "hpad", "afld", "spen",
    "syrd", "silo", "kenn", "pbox", "hbox", "gun", "ftur", "tsla", "agun", "sam", "gap", "iron", "pdox", "mslo",
}


def _types(entries) -> list[str]:
    return [e.get("type", "") for e in entries or []]


def _army(entries) -> list[dict]:
    """Military units: known unit types that fight."""
    return [e for e in entries or [] if e.get("type") in COST and e["type"] not in NOT_ARMY
            and e["type"] not in COST_BUILDINGS]


class OpenRAEncoder:
    """Shared features, one call per decision step (every 250 ticks)."""

    def __init__(self):
        # (stock, value created, kills_cost, deaths_cost) of the last minute of steps
        self.history: deque[tuple[float, float, float, float]] = deque(maxlen=WINDOW_STEPS + 1)

    def step(self, st: dict) -> list[float]:
        eco, mil = st.get("economy", {}), st.get("military", {})
        units, buildings = st.get("units_summary", []), st.get("buildings_summary", [])
        enemies, enemy_buildings = st.get("enemy_summary", []), st.get("enemy_buildings_summary", [])

        stock = (eco.get("cash", 0) + eco.get("ore", 0)) / 100
        # OpenRA reports no gathered/spent totals: what was spent shows up as
        # new assets (or as losses, if already destroyed again)
        created = (mil.get("assets_value", 0) + mil.get("deaths_cost", 0)) / 100
        kills, deaths = mil.get("kills_cost", 0) / 100, mil.get("deaths_cost", 0) / 100
        self.history.append((stock, created, kills, deaths))
        first = self.history[0]
        minutes = max(1, len(self.history) - 1) * STEP_SECONDS / 60
        spending = max(0.0, created - first[1]) / minutes
        income = max(0.0, (stock - first[0]) + (created - first[1])) / minutes
        window = list(self.history)[-(WINDOW_STEPS + 1):]
        killed = kills - window[0][2] if len(window) > 1 else kills
        lost = deaths - window[0][3] if len(window) > 1 else deaths

        types = _types(buildings)
        production = [t for t in types if t in PRODUCTION]
        unit_items = [p for p in st.get("production_items", []) if p.split("@")[0] not in COST_BUILDINGS]
        provided, drained = eco.get("power_provided", 0), eco.get("power_drained", 0)
        army = _army(units)
        enemy_army = _army(enemies)
        home = [(b["cell_x"], b["cell_y"]) for b in buildings if "cell_x" in b]

        def at_home(e):
            return any(math.hypot(e["cell_x"] - x, e["cell_y"] - y) <= HOME_CELLS for x, y in home)

        tech = 1.0 if any(t in TECH_HIGH for t in types) else 0.5 if any(t in TECH_MID for t in types) else 0.0
        features = {
            "t_min": st.get("tick", 0) / TICKS_PER_SECOND / 60,
            "stock": stock,
            "income": income,
            "spending": spending,
            "gatherers": sum(1 for u in units if u.get("type") in WORKERS and u.get("activity")),
            "workers": sum(1 for u in units if u.get("type") in WORKERS),
            "eco_buildings": sum(1 for t in types if t in ECONOMY),
            "supply_headroom": max(0.0, (provided - drained) / provided) if provided > 0 else 0.0,
            "production_buildings": len(production),
            "production_busy": min(1.0, len(unit_items) / len(production)) if production else 0.0,
            "bases": sum(1 for t in types if t in BASES),
            "army_count": len(army),
            "army_value": mil.get("army_value", sum(COST[u["type"]] for u in army)) / 100,
            "defenses": sum(1 for t in types if t in DEFENSES),
            "tech": tech,
            "enemy_army_seen": sum(COST[e["type"]] for e in enemy_army) / 100,
            "enemy_buildings_seen": len(enemy_buildings),
            "killed_value": max(0.0, killed),
            "lost_value": max(0.0, lost),
            "threat_home": sum(COST[e["type"]] for e in enemy_army if "cell_x" in e and at_home(e)) / 100,
            "explored": st.get("explored_percent", 0.0) / 100,
        }
        assert list(features) == FEATURES
        return [round(float(v), 3) for v in features.values()]


# ------------------------------------------------------------------ executor
INFANTRY = ["e3", "e1", "e2", "e4", "shok"]
VEHICLES = ["3tnk", "2tnk", "1tnk", "4tnk", "ttnk", "ctnk", "v2rl", "arty", "ftrk", "jeep", "apc"]


class OpenRAExecutor:
    """Macro actions as OpenRA-RL tool calls. ``call(tool, **args)`` is an
    async function returning the tool's result (a dict)."""

    def __init__(self, call, map_analysis: dict | None = None):
        self.call = call
        self.turn = 0
        self.failures: list[str] = []  # why actions could not be carried out (for diagnosis)
        # enemy buildings seen: id -> cell, until one of our units stands there and sees nothing
        self.sightings: dict[int, tuple[int, int]] = {}
        # places to look for the enemy, most likely first: its estimated base, then far resource fields
        m = map_analysis or {}
        guess = m.get("enemy_estimated_position")
        self.first = [(guess["x"], guess["y"])] if guess else []
        self.far_fields = [(p["center_x"], p["center_y"]) for p in m.get("resource_patches", [])
                           if not p.get("near_base")]
        self.size = (m.get("width", 0), m.get("height", 0))
        self.search = self._sweep()
        # the current target, the closest our army got to it, and attack orders since it got closer
        self.chasing: tuple[tuple[int, int], float, int] | None = None

    def _update_sightings(self, st: dict) -> None:
        visible = {b["id"]: (b["cell_x"], b["cell_y"]) for b in st.get("enemy_buildings_summary", [])}
        self.sightings.update(visible)
        ours = [(u["cell_x"], u["cell_y"]) for u in st.get("units_summary", []) if "cell_x" in u]

        def checked(cell):
            return any(math.hypot(cell[0] - x, cell[1] - y) <= 4 for x, y in ours)

        for bid, cell in list(self.sightings.items()):
            if bid not in visible and checked(cell):
                del self.sightings[bid]  # destroyed, or it was never there
        self.first = [cell for cell in self.first if not checked(cell)]
        self.search = [cell for cell in self.search if not checked(cell)]

    def _sweep(self) -> list[tuple[int, int]]:
        """Places to look when no enemy building is known: far resource fields,
        then a grid over the whole map."""
        w, h = self.size
        grid = [(x, y) for x in range(8, w, 16) for y in range(8, h, 16)] if w and h else []
        return self.far_fields + grid

    def target(self, st: dict) -> tuple[int, int] | None:
        """Where to attack: the nearest known enemy building, else the next place to look.
        A target the army gets no closer to in 6 orders (a minute) is given up: it
        can't be reached, e.g. across water."""
        army = [(u["cell_x"], u["cell_y"]) for u in _army(st.get("units_summary", []))]
        if not army:
            return None
        cx, cy = sum(x for x, _ in army) / len(army), sum(y for _, y in army) / len(army)
        for _ in range(100):
            if self.sightings:
                t = min(self.sightings.values(), key=lambda c: math.hypot(c[0] - cx, c[1] - cy))
            elif self.first:
                t = self.first[0]
            else:
                if not self.search:
                    self.search = self._sweep()  # looked everywhere: sweep again
                home = [(b["cell_x"], b["cell_y"]) for b in st.get("buildings_summary", []) if "cell_x" in b]
                away = [c for c in self.search
                        if not any(math.hypot(c[0] - x, c[1] - y) <= HOME_CELLS for x, y in home)]
                if not away:
                    return self._opposite(st)
                t = min(away, key=lambda c: math.hypot(c[0] - cx, c[1] - cy))
            near = min(math.hypot(t[0] - x, t[1] - y) for x, y in army)
            if self.chasing is None or self.chasing[0] != t:
                self.chasing = (t, near, 0)
                return t
            _, best, stalled = self.chasing
            self.chasing = (t, near, 0) if near < best - 1 else (t, best, stalled + 1)
            if self.chasing[2] < 6:
                return t
            self.sightings = {k: v for k, v in self.sightings.items() if v != t}
            self.first = [c for c in self.first if c != t]
            self.search = [c for c in self.search if c != t]
            self.chasing = None
        return None

    async def act(self, st: dict, counts: dict[str, int]) -> list[str]:
        self.turn += 1
        self._update_sightings(st)
        done = []
        for action in ACTIONS:
            n = int(counts.get(action, 0))
            if n > 0:
                what = await getattr(self, action.lower())(st, n)
                if what:
                    done.append(f"{action}: {what}")
        return done

    # --- helpers
    @staticmethod
    def _available(st: dict) -> list[str]:
        return st.get("available_production", [])

    @staticmethod
    def _have(st: dict, types) -> int:
        return sum(1 for b in st.get("buildings_summary", []) if b.get("type") in types)

    def _failed(self, what: str, why) -> None:
        self.failures.append(f"{what}: {why}"[:160])

    async def _build(self, st: dict, choices: list[str]) -> str | None:
        """Start one structure. Red Alert builds one structure (and one defence) at a
        time: while one is under way, a new order would only queue behind it."""
        underway = [p.split("@")[0] for p in st.get("production_items", [])]
        kind = DEFENSES if set(choices) <= DEFENSES else COST_BUILDINGS - DEFENSES
        busy = [t for t in underway if t in kind]
        if busy:
            return f"{busy[0]} under way"
        for t in choices:
            if t in self._available(st):
                r = await self.call("build_and_place", building_type=t)
                if isinstance(r, dict) and r.get("error"):
                    self._failed(t, r["error"])
                    return None
                return t
        self._failed("/".join(choices), "not available")
        return None

    async def _train(self, st: dict, unit: str, n: int) -> str | None:
        if unit not in self._available(st):
            self._failed(unit, "not available")
            return None
        r = await self.call("build_unit", unit_type=unit, count=n)
        if isinstance(r, dict) and r.get("error"):
            self._failed(unit, r["error"])
            return None
        return f"{n} {unit}"

    def _base(self, st: dict) -> tuple[int, int] | None:
        b = [b for b in st.get("buildings_summary", []) if b.get("type") in BASES] or st.get("buildings_summary", [])
        return (b[0]["cell_x"], b[0]["cell_y"]) if b else None

    def _army_ids(self, st: dict) -> list[int]:
        return [u["id"] for u in _army(st.get("units_summary", []))]

    # --- the macro actions
    async def train_worker(self, st, n):
        return await self._train(st, "harv", min(n, 2))

    async def train_army(self, st, n):
        # alternate infantry and the first vehicle on the list that can be built
        options = (VEHICLES + INFANTRY) if self.turn % 2 else (INFANTRY + VEHICLES)
        for unit in options:
            if unit in self._available(st):
                return await self._train(st, unit, min(n, 5))
        return None

    async def build_supply(self, st, n):
        return await self._build(st, ["apwr", "powr"])

    async def build_economy(self, st, n):
        return await self._build(st, ["proc"] if self._have(st, {"proc"}) < 3 else ["silo"])

    async def build_production(self, st, n):
        if not self._have(st, {"tent", "barr"}):
            return await self._build(st, ["tent", "barr"])
        if not self._have(st, {"weap"}):
            return await self._build(st, ["weap"])
        return await self._build(st, ["weap", "tent", "barr"] if self.turn % 2 else ["tent", "barr", "weap"])

    async def build_defense(self, st, n):
        return await self._build(st, ["gun", "tsla", "pbox", "ftur", "hbox", "agun", "sam"])

    async def tech_up(self, st, n):
        if not self._have(st, TECH_MID):
            return await self._build(st, ["dome"])
        return await self._build(st, ["atek", "stek"])

    async def expand(self, st, n):
        idle = [u for u in st.get("units_summary", []) if u.get("type") == "mcv" and u.get("idle")]
        if idle:
            await self.call("deploy_unit", unit_id=idle[0]["id"])
            return "deployed an MCV"
        return await self._train(st, "mcv", 1)

    async def attack(self, st, n):
        ids = self._army_ids(st)
        target = self.target(st)
        if not ids or target is None:
            return None
        await self.call("attack_move", unit_ids=",".join(map(str, ids)), target_x=target[0], target_y=target[1])
        return f"{len(ids)} units to {target}"

    async def defend(self, st, n):
        ids, base = self._army_ids(st), self._base(st)
        if not ids or base is None:
            return None
        await self.call("attack_move", unit_ids=",".join(map(str, ids)), target_x=base[0], target_y=base[1])
        return f"{len(ids)} units home"

    def _opposite(self, st: dict) -> tuple[int, int] | None:
        """Without a sighting: the point opposite our base across the map centre."""
        base, m = self._base(st), st.get("map", {})
        if base is None or not m.get("width"):
            return None
        return (m["width"] - 1 - base[0], m["height"] - 1 - base[1])

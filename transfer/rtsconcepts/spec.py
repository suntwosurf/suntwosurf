"""The shared vocabulary of both games. The 0 A.D. recorder writes exactly
these names (zeroad/zadbot/mod/zadbot/simulation/ai/zadbot/rts.js, checked by
tests/test_spec.py); the OpenRA encoder computes the same features.

Money is in units of 100 resources: about one basic soldier in both games
(0 A.D. infantry ~100 resources, a Red Alert rifleman $100).
"""

SPEC_VERSION = 1
STEP_SECONDS = 10  # one decision step = 10 s of game time

# What the student may see.
FEATURES = [
    "t_min",                 # game time, minutes
    "stock",                 # resources in stock / 100
    "income",                # gathered in the last minute / 100
    "spending",              # spent in the last minute / 100
    "gatherers",             # units gathering now
    "workers",               # units that can gather
    "eco_buildings",         # dropsites, farms, fields ... / refineries, silos
    "supply_headroom",       # 0 A.D.: 1 - pop / pop cap; OpenRA: power surplus / power produced
    "production_buildings",  # military production buildings
    "production_busy",       # share of them producing
    "bases",                 # civic centres / construction yards
    "army_count",            # military units
    "army_value",            # their cost / 100
    "defenses",              # towers, fortresses / pillboxes, turrets
    "tech",                  # 0, 0.5, 1
    "enemy_army_seen",       # cost / 100 of enemy military units in sight
    "enemy_buildings_seen",  # enemy structures in sight
    "killed_value",          # cost / 100 destroyed of the enemy in the last minute
    "lost_value",            # cost / 100 lost in the last minute
    "threat_home",           # cost / 100 of enemy military units near own structures
    "explored",              # share of the map explored
]

# The truth the student doesn't see: for concept labels only.
PRIVILEGED = [
    "enemy_army_value",
    "enemy_buildings",
    "enemy_stock",
    "enemy_income",
]

# Macro decisions; one step records how many of each the teacher made.
ACTIONS = [
    "TRAIN_WORKER", "TRAIN_ARMY", "BUILD_SUPPLY", "BUILD_ECONOMY", "BUILD_PRODUCTION",
    "BUILD_DEFENSE", "TECH_UP", "EXPAND", "ATTACK", "DEFEND",
]

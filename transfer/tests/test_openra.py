"""The OpenRA side without a game: the encoder on a made-up get_game_state
result, and the executor's tool calls with a fake OpenRA-RL."""

import asyncio

import pytest

from rtsconcepts.openra import OpenRAEncoder, OpenRAExecutor
from rtsconcepts.policies import RulePolicy
from rtsconcepts.spec import ACTIONS, FEATURES


def state(tick=250, cash=3000, ore=500, assets=10000, deaths=0, kills=0, enemies=(), enemy_buildings=(),
          production=(), available=("powr", "proc", "tent", "weap", "harv", "e1", "e3", "1tnk", "pbox", "dome")):
    return {
        "tick": tick, "done": False, "result": "",
        "economy": {"cash": cash, "ore": ore, "power_provided": 200, "power_drained": 150, "harvester_count": 2},
        "military": {"army_value": 1700, "assets_value": assets, "kills_cost": kills, "deaths_cost": deaths},
        "units_summary": [
            {"id": 1, "type": "harv", "activity": "FindAndDeliverResources", "cell_x": 12, "cell_y": 12},
            {"id": 2, "type": "harv", "activity": "", "cell_x": 12, "cell_y": 13},
            {"id": 3, "type": "e1", "activity": "", "cell_x": 14, "cell_y": 14},
            {"id": 4, "type": "1tnk", "activity": "", "cell_x": 15, "cell_y": 14},
            {"id": 5, "type": "e6", "activity": "", "cell_x": 15, "cell_y": 15},  # engineer: not army
            {"id": 6, "type": "mcv", "activity": "", "cell_x": 16, "cell_y": 15, "idle": True},
        ],
        "buildings_summary": [
            {"id": 10, "type": "fact", "cell_x": 10, "cell_y": 10},
            {"id": 11, "type": "powr", "cell_x": 8, "cell_y": 10},
            {"id": 12, "type": "proc", "cell_x": 13, "cell_y": 10},
            {"id": 13, "type": "tent", "cell_x": 10, "cell_y": 14},
            {"id": 14, "type": "pbox", "cell_x": 16, "cell_y": 10},
            {"id": 15, "type": "dome", "cell_x": 6, "cell_y": 6},
        ],
        "enemy_summary": list(enemies), "enemy_buildings_summary": list(enemy_buildings),
        "production_items": list(production), "available_production": list(available),
        "explored_percent": 12.5, "map": {"width": 64, "height": 64},
    }


def test_encoder_features():
    enc = OpenRAEncoder()
    f0 = dict(zip(FEATURES, enc.step(state())))
    assert f0["t_min"] == pytest.approx(250 / 25 / 60, abs=1e-3)
    assert f0["stock"] == 35 and f0["income"] == 0 and f0["spending"] == 0
    assert (f0["gatherers"], f0["workers"], f0["eco_buildings"]) == (1, 2, 1)
    assert f0["supply_headroom"] == 0.25 and f0["production_buildings"] == 1 and f0["bases"] == 1
    assert (f0["army_count"], f0["army_value"], f0["defenses"], f0["tech"]) == (2, 17, 1, 0.5)
    assert f0["explored"] == 0.125
    # 10 s later: stock -500, 1000 of new assets -> spent 1000, gathered 500, per minute x6
    enemies = [{"id": 90, "type": "3tnk", "cell_x": 20, "cell_y": 10}, {"id": 91, "type": "e1", "cell_x": 60, "cell_y": 60},
               {"id": 92, "type": "harv", "cell_x": 11, "cell_y": 11}]
    f1 = dict(zip(FEATURES, enc.step(state(tick=500, cash=2500, ore=500, assets=11000, kills=700, deaths=300,
                                           enemies=enemies, enemy_buildings=[{"id": 95, "cell_x": 50, "cell_y": 50}],
                                           production=["e1@50%(~100 ticks)", "proc@10%(~800 ticks)"]))))
    assert f1["spending"] == pytest.approx((10 + 3) * 6) and f1["income"] == pytest.approx((-5 + 13) * 6)
    assert (f1["killed_value"], f1["lost_value"]) == (7, 3)
    assert f1["enemy_army_seen"] == 12.5  # heavy tank + rifleman; the harvester is no army
    assert f1["enemy_buildings_seen"] == 1
    assert f1["threat_home"] == 11.5  # only the tank is within 15 cells of our buildings
    assert f1["production_busy"] == 1.0  # one unit in production for one production building


def run_executor(counts, st, analysis=None):
    calls = []

    async def call(tool, **args):
        calls.append((tool, args))
        return {}

    ex = OpenRAExecutor(call, analysis)
    done = asyncio.run(ex.act(st, counts))
    return ex, calls, done


def test_executor_calls():
    ex, calls, done = run_executor({"BUILD_SUPPLY": 1, "TRAIN_WORKER": 3, "TRAIN_ARMY": 3, "EXPAND": 1},
                                   state(), {"enemy_estimated_position": {"x": 50, "y": 50}})
    assert ("build_and_place", {"building_type": "powr"}) in calls
    assert ("build_unit", {"unit_type": "harv", "count": 2}) in calls  # at most 2 at once
    assert any(c == ("build_unit", {"unit_type": "1tnk", "count": 3}) for c in calls)  # first turn: vehicles first
    assert ("deploy_unit", {"unit_id": 6}) in calls  # EXPAND with an idle MCV deploys it
    assert len(done) == 4 and not ex.failures
    # TECH_UP after a radar dome: the tech centre isn't available -> a recorded failure, no call
    ex, calls, _ = run_executor({"TECH_UP": 1}, state())
    assert not calls and ex.failures == ["atek/stek: not available"]


def test_one_structure_at_a_time():
    _, calls, done = run_executor({"BUILD_ECONOMY": 1, "BUILD_DEFENSE": 1},
                                  state(production=["proc@10%(~800 ticks)"]))
    assert calls == [("build_and_place", {"building_type": "pbox"})]  # defences have their own queue
    assert done[0] == "BUILD_ECONOMY: proc under way"


def test_attack_targets_and_scouting():
    analysis = {"enemy_estimated_position": {"x": 50, "y": 50}, "width": 64, "height": 64,
                "resource_patches": [{"center_x": 40, "center_y": 8, "near_base": False},
                                     {"center_x": 8, "center_y": 30, "near_base": True}]}
    calls = []

    async def call(tool, **args):
        calls.append((tool, args))
        return {}

    ex = OpenRAExecutor(call, analysis)
    st = state()
    assert ex.target(st) == (50, 50)  # the estimated enemy base first
    st2 = state(enemy_buildings=[{"id": 95, "type": "powr", "cell_x": 30, "cell_y": 30}])
    asyncio.run(ex.act(st2, {"ATTACK": 1}))
    assert calls[-1] == ("attack_move", {"unit_ids": "3,4", "target_x": 30, "target_y": 30})
    # no progress towards a target in 6 more orders: given up (e.g. across water); then
    # the nearest place to look that isn't next to our own buildings
    ex2 = OpenRAExecutor(call, analysis)
    targets = [ex2.target(st) for _ in range(7)]
    assert targets[:6] == [(50, 50)] * 6 and targets[6] == (24, 24)


def test_rule_policy_opening():
    f = dict.fromkeys(FEATURES, 0.0)
    f.update(supply_headroom=1.0, t_min=0.5)
    a = RulePolicy()([f[k] for k in FEATURES])
    assert a == {"BUILD_ECONOMY": 1}
    assert set(a) <= set(ACTIONS)

// Run maps/scripts/ZadbotReport.js in a mocked simulation and check what it
// prints: start, ticks, the time limit decision, and one "end" line once all
// players are won/defeated.   node check_report.mjs <path to ZadbotReport.js>
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function makeSim(victoryConditions)
{
	const printed = [];
	const timers = [];
	let now = 0;
	const players = {
		1: { "state": "active", "pop": 50, "civ": "athen", "gathered": 10000, "kills": 2000 },
		2: { "state": "active", "pop": 40, "civ": "brit", "gathered": 8000, "kills": 500 },
	};
	function Trigger() {}
	const cmpTrigger = Object.create(Trigger.prototype);
	cmpTrigger.registered = [];
	Trigger.prototype.RegisterTrigger = function(event, name, data) { this.registered.push([event, name, data.enabled]); };
	Trigger.prototype.DoRepeatedly = function(time, action) { timers.push({ "repeat": time, action }); };
	Trigger.prototype.DoAfterDelay = function(time, action) { timers.push({ "after": time, action }); };

	const IID = { "IID_Trigger": "Trigger", "IID_Timer": "Timer", "IID_PlayerManager": "PlayerManager",
		"IID_EndGameManager": "EndGameManager", "IID_Identity": "Identity", "IID_Player": "Player",
		"IID_StatisticsTracker": "StatisticsTracker" };
	const active = () => Object.keys(players).map(Number).filter(p => players[p].state == "active");
	// like Player.prototype.SetState: change state, then send the message to triggers
	function setState(pid, state)
	{
		players[pid].state = state;
		cmpTrigger.ZadbotPlayerStateChanged({ "playerId": pid });
	}
	const system = {
		"Trigger": cmpTrigger,
		"Timer": { "GetTime": () => now },
		"PlayerManager": { "GetNonGaiaPlayers": () => [1, 2], "GetActivePlayers": active },
		"EndGameManager": {
			"MarkPlayersAsWon": (winners, victoryString, defeatString) => {
				assert.equal(typeof victoryString(1), "string");
				assert.equal(typeof defeatString(1), "string");
				for (const w of winners)
					setState(w, "won");
				for (const p of active())
					setState(p, "defeated");
			},
		},
	};
	const context = {
		...IID, "SYSTEM_ENTITY": 1, Trigger, JSON, Math, Object, Infinity,
		"print": s => printed.push(s),
		"Resources": { "GetCodes": () => ["food", "wood", "stone", "metal"] },
		"InitAttributes": { "settings": { "VictoryConditions": victoryConditions, "mapName": "Mainland",
			"Seed": 5, "AISeed": 6, "PlayerData": [{ "AI": "zadbot", "AIDiff": 3 }, { "AI": "petra", "AIDiff": 3 }] } },
		"Engine": {
			"QueryInterface": (ent, iid) => ent == 1 ? system[iid] : { "GetCiv": () => players[ent - 100].civ },
		},
		"QueryPlayerIDInterface": (pid, iid = "Player") => iid == "Player" ?
			{ "entity": 100 + pid, "GetState": () => players[pid].state, "GetPopulationCount": () => players[pid].pop } :
			{ "GetStatistics": () => ({
				"resourcesGathered": { "food": players[pid].gathered / 2, "wood": players[pid].gathered / 2, "stone": 0, "metal": 0, "vegetarianFood": 999 },
				"tradeIncome": 0, "enemyUnitsKilledValue": players[pid].kills, "unitsCapturedValue": 0,
				"enemyBuildingsDestroyedValue": 0, "buildingsCapturedValue": 0, "percentMapExplored": 30,
				"unitsTrained": { "total": 80 }, "unitsLost": { "total": 20 }, "enemyUnitsKilled": { "total": 30 },
				"buildingsLost": { "total": 1 }, "enemyBuildingsDestroyed": { "total": 2 },
				"unitsLostValue": 1000, "buildingsLostValue": 300, "percentMapControlled": 12 }) },
	};
	vm.createContext(context);
	return { context, cmpTrigger, printed, timers, players, setState, "setTime": t => { now = t; } };
}

const code = readFileSync(process.argv[2], "utf8");
const lines = sim => sim.printed.map(s => { assert.ok(s.startsWith("ZADBOT ") && s.endsWith("\n")); return JSON.parse(s.slice(7)); });

// --- time limit decides
{
	const sim = makeSim(["conquest", "zadbot_limit_30"]);
	vm.runInContext(code, sim.context);
	assert.deepEqual(sim.cmpTrigger.registered.map(r => r[0]).sort(), ["OnInitGame", "OnPlayerDefeated", "OnPlayerWon"]);
	sim.cmpTrigger.ZadbotInit();
	assert.deepEqual(sim.timers, [{ "repeat": 60000, "action": "ZadbotTick" }, { "after": 1800000, "action": "ZadbotTimeLimit" }]);
	sim.setTime(60000);
	sim.cmpTrigger.ZadbotTick();
	sim.setTime(1800000);
	sim.cmpTrigger.ZadbotTimeLimit();
	sim.cmpTrigger.ZadbotTick();  // no ticks after the end
	const out = lines(sim);
	assert.deepEqual(out.map(l => l.event), ["start", "tick", "end"]);
	assert.equal(out[0].limit, 30);
	const p1 = out[1].players[0];
	// summary-screen formula: (gathered w/o vegetarianFood + trade)/10 + (kills...)/10 + explored*10
	assert.equal(p1.economy, 1000);
	assert.equal(p1.military, 200);
	assert.equal(p1.exploration, 300);
	assert.equal(p1.score, 1500);
	assert.equal(p1.ai, "zadbot");
	assert.equal(p1.civ, "athen");
	assert.equal(out[2].reason, "time_limit");
	assert.deepEqual(out[2].winners, [1]);
	assert.equal(out[2].time, 1800);
}

// --- conquest: player 1 is defeated, then player 2 is marked won
{
	const sim = makeSim(["conquest", "zadbot_report"]);
	vm.runInContext(code, sim.context);
	sim.cmpTrigger.ZadbotInit();
	assert.deepEqual(sim.timers, [{ "repeat": 60000, "action": "ZadbotTick" }]);
	sim.setTime(1500000);
	sim.setState(1, "defeated");
	sim.setState(2, "won");
	const out = lines(sim);
	assert.deepEqual(out.map(l => l.event), ["start", "defeated", "end"]);
	assert.equal(out[0].limit, null);
	assert.equal(out[1].player, 1);
	assert.equal(out[2].reason, "conquest");
	assert.deepEqual(out[2].winners, [2]);
}

// --- a tie at the time limit: both win, the match still ends
{
	const sim = makeSim(["conquest", "zadbot_limit_20"]);
	vm.runInContext(code, sim.context);
	sim.players[2].gathered = sim.players[1].gathered;
	sim.players[2].kills = sim.players[1].kills;
	sim.cmpTrigger.ZadbotInit();
	sim.cmpTrigger.ZadbotTimeLimit();
	const out = lines(sim);
	assert.deepEqual(out.at(-1).winners, [1, 2]);
}
console.log("ok");

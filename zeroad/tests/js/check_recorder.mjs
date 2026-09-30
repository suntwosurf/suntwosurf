// The recorder (rts.js, recorder.js) on the real Petra modules with a made-up
// game state; prints the logged lines and checks as JSON for tests/test_js.py.
//
//   ZADBOT_JS_ROOTS=<mod root>:<Petra root> node --import ./register.mjs check_recorder.mjs

const logged = [];
globalThis.log = msg => logged.push(String(msg));
globalThis.warn = msg => logged.push("WARN " + msg);
globalThis.uneval = x => JSON.stringify(x);
globalThis.Resources = class { GetCodes() { return ["food", "wood", "stone", "metal"]; } };
globalThis.markForTranslation = x => x;
globalThis.markForTranslationWithContext = (c, x) => x;
globalThis.markForPluralTranslation = (a, b) => a;
Math.square = x => x * x;
globalThis.randFloat = (lo, hi) => (lo + hi) / 2;
globalThis.Sim = { "SYSTEM_ENTITY": 1, "IID_ModifiersManager": "ModifiersManager" };
globalThis.SimEngine = { "QueryInterface": () => ({ "AddModifiers": () => {} }) };

const { Queue } = await import("simulation/ai/petra/queue.js");
const { QueueManager } = await import("simulation/ai/petra/queueManager.js");
const { Config } = await import("simulation/ai/petra/config.js");
const { RecorderBot } = await import("simulation/ai/zadbot_rec/_rec.js");
const rts = await import("simulation/ai/zadbot/rts.js");

const addPlanBefore = Queue.prototype.addPlan;
const addQueueBefore = QueueManager.prototype.addQueue;

// --- a made-up game: player 1 (us) vs player 2, gaia animals around
function ent(owner, classes, x, z, extra = {})
{
	return {
		"owner": () => owner,
		"hasClass": c => classes.includes(c),
		"costSum": () => extra.cost ?? 100,
		"position": () => [x, z],
		"visionRange": () => extra.vision ?? 0,
		"unitAIState": () => extra.state ?? "INDIVIDUAL.IDLE",
		"trainingQueue": () => extra.queue ?? [],
		"foundationProgress": () => extra.foundation,
	};
}
const coll = arr => ({ "toEntityArray": () => arr });
const ownUnits = [
	ent(1, ["Unit", "Worker", "FemaleCitizen"], 10, 10, { "state": "INDIVIDUAL.GATHER.GATHERING", "cost": 50, "vision": 20 }),
	ent(1, ["Unit", "Worker", "FemaleCitizen"], 12, 10, { "state": "INDIVIDUAL.GATHER.APPROACHING", "cost": 50, "vision": 20 }),
	ent(1, ["Unit", "Worker", "Soldier", "Infantry"], 14, 10, { "cost": 100, "vision": 80 }),
	ent(1, ["Unit", "Soldier", "Cavalry"], 200, 200, { "cost": 150, "vision": 90 }),
];
const ownStructures = [
	ent(1, ["Structure", "CivCentre", "Defensive"], 0, 0, { "cost": 500, "vision": 90 }),
	ent(1, ["Structure", "Barracks"], 30, 0, { "cost": 300, "queue": [{}] }),
	ent(1, ["Structure", "Stable"], 40, 0, { "cost": 200, "foundation": 50 }),  // a foundation: not counted
	ent(1, ["Structure", "Tower"], 0, 40, { "cost": 100 }),
	ent(1, ["Structure", "Storehouse"], 20, 20, { "cost": 100 }),
	ent(1, ["Structure", "Field"], 25, 25, { "cost": 100 }),
];
const enemyUnits = [
	ent(2, ["Unit", "Soldier"], 50, 0, { "cost": 100 }),      // near our base, seen by the soldier (vision 80)
	ent(2, ["Unit", "Soldier"], 1000, 1000, { "cost": 120 }), // far away, unseen
	ent(0, ["Unit", "Animal"], 5, 5, { "cost": 0 }),          // gaia: never an enemy
];
const enemyStructures = [
	ent(2, ["Structure", "CivCentre"], 280, 200, { "cost": 500 }), // within the cavalry's vision
	ent(2, ["Structure", "House"], 1000, 1000, { "cost": 150 }),
	ent(0, ["Structure", "Ruins"], 1, 1, { "cost": 0 }),
];
let elapsed = 0;
let gathered = 0;
let stock = 300;
const gameState = {
	"playerData": {
		get "resourceCounts"() { return { "food": stock, "wood": 0, "stone": 0, "metal": 0 }; },
		get "statistics"() { return { "resourcesGathered": { "food": gathered, "wood": 0, "stone": 0, "metal": 0, "vegetarianFood": 999 }, "percentMapExplored": 25 }; },
		"popCount": 30, "popLimit": 40, "state": "active",
	},
	"sharedScript": { "playersData": { "2": { "resourceCounts": { "food": 700 }, "statistics": { "resourcesGathered": { "food": 0 } } } } },
	"getEnemies": () => [0, 2],
	"getOwnUnits": () => coll(ownUnits),
	"getOwnStructures": () => coll(ownStructures),
	"getEnemyUnits": () => coll(enemyUnits),
	"getEnemyStructures": () => coll(enemyStructures),
	"getNumberOfPhases": () => 3,
	"currentPhase": () => 2,
	"getTimeElapsed": () => elapsed,
	"getPlayerCiv": () => "athen",
};

// --- the recorder on a real Petra queue manager, without the rest of Petra
const config = new Config(3, "balanced");
const queues = {};
for (const name in config.priorities)
	queues[name] = new Queue();
const bot = Object.create(RecorderBot.prototype);
bot.player = 1;
bot.Config = config;
bot.gameState = gameState;
bot.queueManager = new QueueManager(config, queues);
bot.rec = { "mem": rts.newMemory(), "next": 0, "actions": {}, "attacks": new Set(), "armies": new Set(), "header": false, "failed": false };
bot.watchQueues();
bot.HQ = { "attackManager": { "startedAttacks": { "Rush": [{ "name": 7 }], "Attack": [] } }, "defenseManager": { "armies": [] } };

const plan = (type, category, number = 1) => ({ type, category, number, "maxMerge": 5, "addItem": function(n) { this.number += n; } });
queues.villager.addPlan(plan("units/athen/support_female_citizen", "unit", 5));
queues.house.addPlan(plan("structures/athen/house", "building"));
queues.majorTech.addPlan(plan("phase_town_generic", "technology"));
queues.minorTech.addPlan(plan("gather_wicker_baskets", "technology"));  // not a macro action
bot.queueManager.addQueue("plan_Rush_3", 100);
queues.plan_Rush_3.addPlan(plan("units/athen/infantry_spearman_b", "unit", 4));

// a plain Petra queue (another player) is not watched
const other = new Queue();
other.addPlan(plan("structures/athen/house", "building"));

// losses and kills of one turn
bot.events = { "Destroy": [
	{ "entityObj": ent(1, ["Unit"], 0, 0, { "cost": 50 }) },
	{ "entityObj": ent(2, ["Unit"], 0, 0, { "cost": 80 }) },
	{ "entityObj": ent(1, ["Structure"], 0, 0, { "cost": 300 }), "SuccessfulFoundation": true },  // not a loss
] };
rts.countDestroyed(bot.rec.mem, bot.events.Destroy, 1);

elapsed = 10000;
bot.record(10);
// one step later: gathered 600, stock went up by 100 -> spent 500
elapsed = 20000;
gathered = 600;
stock = 400;
bot.HQ.defenseManager.armies.push({ "ID": 3 });
bot.record(20);

console.log(JSON.stringify({
	"logged": logged,
	"planActions": ["villager", "citizenSoldier", "plan_Attack_1", "emergency", "house", "dropsites", "field",
		"militaryBuilding", "defenseBuilding", "civilCentre", "healer", "ships"].map(q => rts.planAction(q, {})),
	"prototypesUntouched": Queue.prototype.addPlan === addPlanBefore && QueueManager.prototype.addQueue === addQueueBefore,
	"otherQueuePlans": other.plans.length,
	"names": { "features": rts.FEATURES, "privileged": rts.PRIVILEGED, "actions": rts.ACTIONS, "version": rts.SPEC_VERSION },
}));

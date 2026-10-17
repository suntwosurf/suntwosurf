/**
 * zadbot recorder: the game-independent view of an RTS game used for
 * learning concepts that transfer to other games (transfer/DESIGN.md).
 *
 * Every 10 s of game time the recorder writes one line to the game's main log
 * (mainlog*.html, on every OS):
 *   ZADREC {"p": player, "t": seconds, "f": [features], "x": [privileged], "a": {action: count}}
 * The first line of each player is a header with the names below.
 *
 * The names must match transfer/rtsconcepts/spec.py (checked by the tests).
 */

export const SPEC_VERSION = 1;

/** What the student may see. Money is in units of 100 resources (about one basic soldier). */
export const FEATURES = [
	"t_min",                // game time, minutes
	"stock",                // resources in stock / 100
	"income",               // gathered in the last minute / 100
	"spending",             // spent in the last minute / 100
	"gatherers",            // units gathering now
	"workers",              // units that can gather
	"eco_buildings",        // dropsites, farms, fields, corrals, markets, docks
	"supply_headroom",      // 1 - population / population limit
	"production_buildings", // military production buildings
	"production_busy",      // share of them producing
	"bases",                // civic centres
	"army_count",           // military units (0 A.D.: citizen soldiers too)
	"army_value",           // their cost / 100
	"defenses",             // towers, fortresses, wall towers
	"tech",                 // 0, 0.5, 1 for phase 1..3
	"enemy_army_seen",      // cost / 100 of enemy military units inside our vision
	"enemy_buildings_seen", // enemy structures inside our vision
	"killed_value",         // cost / 100 of enemy units and buildings destroyed in the last minute
	"lost_value",           // cost / 100 of own units and buildings lost in the last minute
	"threat_home",          // cost / 100 of enemy military units near our structures
	"explored",             // share of the map explored
];

/** The truth the student doesn't see, for concept labels only. */
export const PRIVILEGED = [
	"enemy_army_value",     // cost / 100 of all enemy military units
	"enemy_buildings",      // all enemy structures
	"enemy_stock",          // enemy resources in stock / 100
	"enemy_income",         // enemy gathered in the last minute / 100
];

export const ACTIONS = [
	"TRAIN_WORKER", "TRAIN_ARMY", "BUILD_SUPPLY", "BUILD_ECONOMY", "BUILD_PRODUCTION",
	"BUILD_DEFENSE", "TECH_UP", "EXPAND", "ATTACK", "DEFEND",
];

export const STEP_SECONDS = 10;
const WINDOW_STEPS = 6;  // "the last minute"
const HOME_RADIUS = 60;  // metres around own structures that count as home

const MILITARY = ["Soldier", "Siege", "Warship"];
const ECONOMY = ["Storehouse", "Farmstead", "Field", "Corral", "Market", "Dock"];
const PRODUCTION = ["Barracks", "Stable", "Range", "ElephantStable", "Arsenal", "Kennel", "Fortress"];
const DEFENSE = ["Tower", "Fortress", "WallTower"];

/**
 * The macro action a plan added to one of Petra's queues stands for, or null.
 * @param {string} queue - the queue name (Petra's Config.priorities keys, or "plan_*" for attack armies).
 * @param {Object} plan - a Petra queue plan (type, category, number).
 */
export function planAction(queue, plan)
{
	if (queue == "villager" || queue == "citizenSoldier")
		return "TRAIN_WORKER";
	if (queue.startsWith("plan_") || queue == "emergency")
		return "TRAIN_ARMY";
	if (queue == "house")
		return "BUILD_SUPPLY";
	if (["dropsites", "field", "economicBuilding", "corral", "dock"].includes(queue))
		return "BUILD_ECONOMY";
	if (queue == "militaryBuilding")
		return "BUILD_PRODUCTION";
	if (queue == "defenseBuilding")
		return "BUILD_DEFENSE";
	if (queue == "civilCentre")
		return "EXPAND";
	if (queue == "majorTech" && typeof plan.type == "string" && plan.type.startsWith("phase_"))
		return "TECH_UP";
	return null;
}

function sum(obj)
{
	let total = 0;
	for (const key in obj)
		if (typeof obj[key] == "number" && key != "vegetarianFood")
			total += obj[key];
	return total;
}

function isAny(ent, classes)
{
	return classes.some(c => ent.hasClass(c));
}

function value(ent)
{
	return (ent.costSum() || 0) / 100;
}

function dist2(a, b)
{
	return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2;
}

/** Enemy (not gaia) entities of a collection. */
function enemies(collection)
{
	return collection.toEntityArray().filter(ent => ent.owner() > 0);
}

/**
 * Memory across steps for one player: stock and gathered totals of the last
 * minute, and values killed/lost since the last step.
 */
export function newMemory()
{
	return { "history": [], "killed": 0, "lost": 0, "killedWindow": [], "lostWindow": [] };
}

/** Add destroyed entities of one turn (Petra's events.Destroy) to the memory. */
export function countDestroyed(mem, destroyEvents, player)
{
	for (const evt of destroyEvents || [])
	{
		if (!evt || evt.SuccessfulFoundation || !evt.entityObj)
			continue;
		const owner = evt.entityObj.owner();
		if (owner == player)
			mem.lost += value(evt.entityObj);
		else if (owner > 0)
			mem.killed += value(evt.entityObj);
	}
}

/**
 * The shared features (FEATURES order) and privileged values (PRIVILEGED order)
 * of this step. Advances the memory by one step.
 */
export function stepFeatures(gameState, mem, player)
{
	const pd = gameState.playerData;
	const stock = sum(pd.resourceCounts) / 100;
	const gathered = sum(pd.statistics?.resourcesGathered) / 100;

	const enemyIDs = gameState.getEnemies().filter(id => id > 0);
	const enemyData = enemyIDs.map(id => gameState.sharedScript.playersData[id]).filter(d => d);
	const enemyStock = enemyData.reduce((s, d) => s + sum(d.resourceCounts) / 100, 0);
	const enemyGathered = enemyData.reduce((s, d) => s + sum(d.statistics?.resourcesGathered) / 100, 0);

	mem.history.push({ stock, gathered, enemyGathered });
	if (mem.history.length > WINDOW_STEPS + 1)
		mem.history.shift();
	mem.killedWindow.push(mem.killed);
	mem.lostWindow.push(mem.lost);
	mem.killed = mem.lost = 0;
	if (mem.killedWindow.length > WINDOW_STEPS)
	{
		mem.killedWindow.shift();
		mem.lostWindow.shift();
	}
	// per minute, even while the history is still shorter than a minute
	const first = mem.history[0];
	const minutes = Math.max(1, mem.history.length - 1) * STEP_SECONDS / 60;
	const income = (gathered - first.gathered) / minutes;
	const spending = Math.max(0, (gathered - first.gathered) - (stock - first.stock)) / minutes;
	const enemyIncome = (enemyGathered - first.enemyGathered) / minutes;

	const units = gameState.getOwnUnits().toEntityArray();
	const structures = gameState.getOwnStructures().toEntityArray();
	const army = units.filter(ent => isAny(ent, MILITARY));
	const production = structures.filter(ent => isAny(ent, PRODUCTION) && !ent.foundationProgress());
	const busy = production.filter(ent => (ent.trainingQueue() || []).length > 0).length;

	const enemyUnits = enemies(gameState.getEnemyUnits());
	const enemyArmy = enemyUnits.filter(ent => isAny(ent, MILITARY) && ent.position());
	const enemyStructures = enemies(gameState.getEnemyStructures());

	// inside our vision: within the vision range of one of our entities
	const eyes = units.concat(structures).filter(ent => ent.position())
		.map(ent => [ent.position(), (ent.visionRange() || 0) ** 2]);
	const seen = ent => ent.position() && eyes.some(([pos, r2]) => dist2(pos, ent.position()) <= r2);
	const home = structures.filter(ent => ent.position()).map(ent => ent.position());
	const atHome = ent => home.some(pos => dist2(pos, ent.position()) <= HOME_RADIUS ** 2);

	const phases = gameState.getNumberOfPhases();
	const popLimit = pd.popLimit || 0;
	const features = [
		gameState.getTimeElapsed() / 60000,
		stock,
		income,
		spending,
		units.filter(ent => (ent.unitAIState() || "").includes("GATHER")).length,
		units.filter(ent => ent.hasClass("Worker")).length,
		structures.filter(ent => isAny(ent, ECONOMY)).length,
		popLimit > 0 ? Math.max(0, 1 - (pd.popCount || 0) / popLimit) : 0,
		production.length,
		production.length ? busy / production.length : 0,
		structures.filter(ent => ent.hasClass("CivCentre") && !ent.foundationProgress()).length,
		army.length,
		army.reduce((s, ent) => s + value(ent), 0),
		structures.filter(ent => isAny(ent, DEFENSE) && !ent.foundationProgress()).length,
		phases > 1 ? Math.max(0, gameState.currentPhase() - 1) / (phases - 1) : 0,
		enemyArmy.filter(seen).reduce((s, ent) => s + value(ent), 0),
		enemyStructures.filter(seen).length,
		mem.killedWindow.reduce((a, b) => a + b, 0),
		mem.lostWindow.reduce((a, b) => a + b, 0),
		enemyArmy.filter(atHome).reduce((s, ent) => s + value(ent), 0),
		(pd.statistics?.percentMapExplored || 0) / 100,
	];
	const privileged = [
		enemyArmy.reduce((s, ent) => s + value(ent), 0),
		enemyStructures.length,
		enemyStock,
		enemyIncome,
	];
	return { features, privileged };
}

/** Rounded for the log: 3 significant decimals are plenty. */
export function compact(values)
{
	return values.map(v => Math.round(v * 1000) / 1000);
}

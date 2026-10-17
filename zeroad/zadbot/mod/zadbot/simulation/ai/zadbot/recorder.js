/**
 * zadbot recorder: plain Petra that writes what it sees and decides to the
 * game's main log, as teacher data for a student that learns RTS concepts
 * (transfer/DESIGN.md, and rts.js for the line format).
 *
 * Petra plays unchanged. The recorder only watches this player's own queues
 * (instance properties, never Petra's prototypes, which other players share)
 * and reads the game state every 10 s.
 */
import { PetraBot } from "simulation/ai/petra/_petrabot.js";
import { Queue } from "simulation/ai/petra/queue.js";
import { QueueManager } from "simulation/ai/petra/queueManager.js";
import {
	ACTIONS, FEATURES, PRIVILEGED, SPEC_VERSION, STEP_SECONDS, compact, countDestroyed, newMemory, planAction,
	stepFeatures
} from "simulation/ai/zadbot/rts.js";

const WATCHED = "zadbotWatched";

function emit(record)
{
	log("ZADREC " + JSON.stringify(record));
}

export function RecorderBot(settings)
{
	PetraBot.call(this, settings);
	this.rec = null;
}

RecorderBot.prototype = Object.create(PetraBot.prototype);
RecorderBot.prototype.constructor = RecorderBot;

RecorderBot.prototype.CustomInit = function(gameState)
{
	PetraBot.prototype.CustomInit.call(this, gameState);
	this.rec = {
		"mem": newMemory(),
		"next": 0,
		"actions": {},
		"attacks": new Set(),
		"armies": new Set(),
		"header": false,
		"failed": false
	};
	this.watchQueues();
};

/** Count every plan Petra adds to one of this player's queues as a macro action. */
RecorderBot.prototype.watchQueues = function()
{
	const rec = this.rec;
	const note = (name, plan) => {
		const action = planAction(name, plan);
		if (action)
			rec.actions[action] = (rec.actions[action] || 0) + (plan.category == "unit" ? plan.number || 1 : 1);
	};
	const watch = (name, queue) => {
		if (!queue || queue[WATCHED])
			return;
		queue[WATCHED] = true;
		queue.addPlan = function(plan)
		{
			if (plan)
				note(name, plan);
			return Queue.prototype.addPlan.call(this, plan);
		};
	};
	const queueManager = this.queueManager;
	for (const name in queueManager.queues)
		watch(name, queueManager.queues[name]);
	// attack plans add their own queues ("plan_<name>") later
	queueManager.addQueue = function(name, priority)
	{
		const result = QueueManager.prototype.addQueue.call(this, name, priority);
		watch(name, this.queues[name]);
		return result;
	};
};

RecorderBot.prototype.OnUpdate = function(sharedScript)
{
	const rec = this.rec;
	if (rec && !rec.failed && this.events)
		countDestroyed(rec.mem, this.events.Destroy, this.player);

	PetraBot.prototype.OnUpdate.call(this, sharedScript);

	if (!rec || rec.failed || this.gameFinished || this.gameState.playerData.state == "defeated")
		return;
	const t = this.gameState.getTimeElapsed() / 1000;
	if (t < rec.next)
		return;
	rec.next = (Math.floor(t / STEP_SECONDS) + 1) * STEP_SECONDS;
	try
	{
		this.record(t);
	}
	catch (e)
	{
		// never disturb the game: report once and stop recording
		rec.failed = true;
		emit({ "p": this.player, "error": String(e), "stack": String(e.stack || "") });
	}
};

RecorderBot.prototype.record = function(t)
{
	const rec = this.rec;
	const gameState = this.gameState;
	if (!rec.header)
	{
		emit({
			"p": this.player, "header": true, "v": SPEC_VERSION, "step": STEP_SECONDS,
			"features": FEATURES, "privileged": PRIVILEGED, "actions": ACTIONS,
			"civ": gameState.getPlayerCiv(), "difficulty": this.Config.difficulty, "behavior": this.Config.behavior,
			"personality": this.Config.personality
		});
		rec.header = true;
	}

	const attackManager = this.HQ.attackManager;
	for (const type in attackManager.startedAttacks)
		for (const attack of attackManager.startedAttacks[type])
			if (!rec.attacks.has(attack.name))
			{
				rec.attacks.add(attack.name);
				rec.actions.ATTACK = (rec.actions.ATTACK || 0) + 1;
			}
	for (const army of this.HQ.defenseManager.armies)
		if (!rec.armies.has(army.ID))
		{
			rec.armies.add(army.ID);
			rec.actions.DEFEND = (rec.actions.DEFEND || 0) + 1;
		}

	const { features, privileged } = stepFeatures(gameState, rec.mem, this.player);
	// "a": what Petra decided since the previous line
	emit({ "p": this.player, "t": Math.round(t), "f": compact(features), "x": compact(privileged), "a": rec.actions });
	rec.actions = {};
};

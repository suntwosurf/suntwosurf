/**
 * zadbot: Petra with learned settings.
 *
 * The bot is the game's own Petra (imported from the installed 0 A.D., so it
 * always matches the game version). Only a few numbers in Petra's Config are
 * changed after Petra has set them up for this game (difficulty, map, pop cap).
 *
 * All AI players in a match share these Petra modules, and an opponent may be
 * plain Petra. So nothing here may change Petra's prototypes: the learned
 * values live on a Config subclass that only zadbot players use.
 */
import { aiWarn } from "simulation/ai/common-api/utils.js";
import { PetraBot } from "simulation/ai/petra/_petrabot.js";
import { Config } from "simulation/ai/petra/config.js";

/**
 * Apply learned values to a Petra Config.
 * @param {Object} config - Petra's Config, after Config.setConfig().
 * @param {Object} learned - { "Economy.popPhase2": { "mode": "scale", "value": 0.8, "round": true, "min": 1 }, ... }
 *   mode "set" replaces Petra's value, mode "scale" multiplies it.
 * @return {string[]} names of the settings this Petra version does not have.
 */
export function applyLearned(config, learned)
{
	const unknown = [];
	for (const key in learned)
	{
		const entry = learned[key];
		const path = key.split(".");
		let owner = config;
		for (let i = 0; i < path.length - 1 && owner; ++i)
			owner = owner[path[i]];
		const leaf = path[path.length - 1];
		if (!owner || typeof owner[leaf] != "number")
		{
			unknown.push(key);
			continue;
		}

		let value = entry.mode == "scale" ? owner[leaf] * entry.value : entry.value;
		if (entry.round)
			value = Math.round(value);
		if (entry.min !== undefined)
			value = Math.max(entry.min, value);
		if (entry.max !== undefined)
			value = Math.min(entry.max, value);
		owner[leaf] = value;
	}

	// Petra's own consistency rules (end of Config.setConfig).
	const economy = config.Economy;
	economy.targetNumWorkers = Math.max(economy.targetNumWorkers, economy.popPhase2);
	if (Number.isFinite(economy.workPhase3))
		economy.workPhase3 = Math.min(economy.workPhase3, economy.targetNumWorkers);
	if (Number.isFinite(economy.workPhase4))
		economy.workPhase4 = Math.min(economy.workPhase4, economy.targetNumWorkers);
	const military = config.Military;
	military.popForBarracks2 = Math.max(military.popForBarracks2, military.popForBarracks1);

	return unknown;
}

export function LearnedConfig(difficulty, behavior, learned)
{
	Config.call(this, difficulty, behavior);
	// Plain data, so it is saved and restored with the rest of the Config.
	this.learned = learned;
}

LearnedConfig.prototype = Object.create(Config.prototype);
LearnedConfig.prototype.constructor = LearnedConfig;

LearnedConfig.prototype.setConfig = function(gameState)
{
	Config.prototype.setConfig.call(this, gameState);
	const unknown = applyLearned(this, this.learned || {});
	// zadbot's match runner looks for this line: it means the learned
	// settings do not fit this Petra version.
	if (unknown.length)
		aiWarn("zadbot: unknown Petra settings: " + unknown.join(", "));
};

/**
 * @param {Object} learned - the learned values (see applyLearned).
 * @return {Function} an AI constructor for data.json.
 */
export function makeLearnedBot(learned)
{
	function LearnedBot(settings)
	{
		PetraBot.call(this, settings);
		this.Config = new LearnedConfig(settings.difficulty, settings.behavior, learned);
	}
	LearnedBot.prototype = Object.create(PetraBot.prototype);
	LearnedBot.prototype.constructor = LearnedBot;
	return LearnedBot;
}

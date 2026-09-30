// Build plain Petra's Config and zadbot's LearnedConfig for the same game
// (same random draws), and print both as JSON for tests/test_js.py.
//
//   ZADBOT_JS_ROOTS=<mod root>:<Petra root> ZADBOT_TEST_ENTRIES='{...}' \
//     node --import ./register.mjs check_learned.mjs <difficulty>

// Globals the AI scripts get from the game (globalscripts/, the AI worker).
const warnings = [];
globalThis.warn = msg => warnings.push(String(msg));
globalThis.uneval = x => JSON.stringify(x);
globalThis.Resources = class { GetCodes() { return ["food", "wood", "stone", "metal"]; } };
globalThis.markForTranslation = x => x;
globalThis.markForTranslationWithContext = (c, x) => x;
globalThis.markForPluralTranslation = (a, b) => a;
Math.square = x => x * x;
let draws = 0;
// deterministic stand-in for the engine's randFloat
globalThis.randFloat = (lo, hi) => lo + (hi - lo) * ((Math.sin(++draws * 12.9898) * 43758.5453) % 1 + 1) % 1;
const modifiers = [];
globalThis.Sim = { "SYSTEM_ENTITY": 1, "IID_ModifiersManager": "ModifiersManager" };
globalThis.SimEngine = { "QueryInterface": () => ({ "AddModifiers": (name, mods, ent) => modifiers.push(name) }) };

const { PetraBot } = await import("simulation/ai/petra/_petrabot.js");
const { Config } = await import("simulation/ai/petra/config.js");
const learnedModule = await import("simulation/ai/zadbot/learned.js");
const { ZadBot } = await import("simulation/ai/zadbot/_zadbot.js");

const difficulty = +(process.argv[2] ?? 3);
const entries = JSON.parse(process.env.ZADBOT_TEST_ENTRIES || "{}");
const gameState = {
	"playerData": { "teamsLocked": false, "entity": 7 },
	"getAlliedVictory": () => false,
	"getPopulationMax": () => 300,
	"getVictoryConditions": () => new Set(["conquest"]),
};
const settings = { "player": 1, "difficulty": difficulty, "behavior": "balanced" };

function snapshot(config)
{
	const out = {};
	for (const key of Object.keys(entries).concat(["Economy.workPhase3", "Economy.workPhase4", "Economy.targetNumWorkers",
		"Economy.popPhase2", "Military.popForBarracks1", "Military.popForBarracks2", "Military.numSentryTowers",
		"Military.towerLapseTime", "priorities.defenseBuilding", "personality.cooperative"]))
	{
		let v = config;
		for (const part of key.split("."))
			v = v?.[part];
		out[key] = v === Infinity ? "Infinity" : v;
	}
	return out;
}

const setConfigBefore = Config.prototype.setConfig;

draws = 0;
const plain = new Config(difficulty, "balanced");
plain.setConfig(gameState);

// Plain Petra that happened to draw the learned personality (same draws
// otherwise): what the learned multipliers are applied to.
draws = 0;
const reference = new Config(difficulty, "balanced");
const traits = {};
for (const key in entries)
	if (key.startsWith("personality."))
		traits[key.slice("personality.".length)] = entries[key].value;
let drawn = reference.personality;
Object.defineProperty(reference, "personality", {
	"get": () => drawn,
	"set": value => { drawn = Object.assign(value, traits); },
	"configurable": true
});
reference.setConfig(gameState);

draws = 0;
const Bot = learnedModule.makeLearnedBot(entries);
const bot = new Bot(settings);
bot.Config.setConfig(gameState);

// A plain Petra made after the bot must be unaffected by it.
draws = 0;
const plainAfter = new Config(difficulty, "balanced");
plainAfter.setConfig(gameState);

const restored = new Bot(settings);
restored.Config.Deserialize(JSON.parse(JSON.stringify(bot.Config.Serialize())));

const defaultBot = new ZadBot(settings);

console.log(JSON.stringify({
	"plain": snapshot(plain),
	"reference": snapshot(reference),
	"learned": snapshot(bot.Config),
	"personalityIsData": "value" in Object.getOwnPropertyDescriptor(bot.Config, "personality"),
	"plainAfter": snapshot(plainAfter),
	"restoredLearned": restored.Config.learned,
	"personalitySaved": JSON.stringify(restored.Config.personality) == JSON.stringify(bot.Config.personality),
	"warnings": warnings,
	"prototypeUntouched": Config.prototype.setConfig === setConfigBefore,
	"botIsPetra": bot instanceof PetraBot,
	"configIsPetraConfig": bot.Config instanceof Config,
	"defaultBotLearned": defaultBot.Config.learned,
	"cheatApplied": modifiers.length,
}));

/**
 * zadbot match report, used by the zadbot_report and zadbot_limit_<N>
 * victory conditions. It only reads the game, except for the time limit.
 *
 * Prints lines "ZADBOT <json>" to stdout for zadbot's match runner:
 *   {"event":"start", "time":0, "limit":30, "players":[...]}
 *   {"event":"tick", "time":60, "players":[...]}          every minute
 *   {"event":"defeated", "time":..., "player":2}
 *   {"event":"end", "time":..., "reason":"conquest"|"time_limit", "winners":[1], "players":[...]}
 *
 * With zadbot_limit_<N> active, the match ends after N minutes and the player
 * with the highest score (the summary screen's total score) wins.
 */

Trigger.prototype.ZadbotPlayerStats = function(playerID)
{
	const cmpPlayer = QueryPlayerIDInterface(playerID);
	const stats = QueryPlayerIDInterface(playerID, IID_StatisticsTracker).GetStatistics();

	// Same formulas as the summary screen (gui/summary/counters.js).
	let gathered = 0;
	for (const type of Resources.GetCodes())
		gathered += stats.resourcesGathered[type];
	const economy = Math.round((gathered + stats.tradeIncome) / 10);
	const military = Math.round((stats.enemyUnitsKilledValue + stats.unitsCapturedValue +
		stats.enemyBuildingsDestroyedValue + stats.buildingsCapturedValue) / 10);
	const exploration = stats.percentMapExplored * 10;

	const playerData = InitAttributes.settings.PlayerData?.[playerID - 1] || {};
	const cmpIdentity = Engine.QueryInterface(cmpPlayer.entity, IID_Identity);
	return {
		"id": playerID,
		"ai": playerData.AI || "",
		"aiDiff": playerData.AIDiff ?? null,
		"civ": cmpIdentity ? cmpIdentity.GetCiv() : "",
		"state": cmpPlayer.GetState(),
		"pop": cmpPlayer.GetPopulationCount(),
		"score": economy + military + exploration,
		"economy": economy,
		"military": military,
		"exploration": exploration,
		"unitsTrained": stats.unitsTrained.total,
		"unitsLost": stats.unitsLost.total,
		"enemyUnitsKilled": stats.enemyUnitsKilled.total,
		"buildingsLost": stats.buildingsLost.total,
		"enemyBuildingsDestroyed": stats.enemyBuildingsDestroyed.total,
		"unitsLostValue": stats.unitsLostValue,
		"buildingsLostValue": stats.buildingsLostValue,
		"percentMapControlled": stats.percentMapControlled
	};
};

Trigger.prototype.ZadbotPrint = function(event, data)
{
	const cmpTimer = Engine.QueryInterface(SYSTEM_ENTITY, IID_Timer);
	const line = { "event": event, "time": Math.round(cmpTimer.GetTime() / 1000) };
	for (const key in data)
		line[key] = data[key];
	print("ZADBOT " + JSON.stringify(line) + "\n");
};

Trigger.prototype.ZadbotAllPlayers = function()
{
	return Engine.QueryInterface(SYSTEM_ENTITY, IID_PlayerManager).GetNonGaiaPlayers().map(
		playerID => this.ZadbotPlayerStats(playerID));
};

Trigger.prototype.ZadbotInit = function()
{
	this.zadbotEnded = false;
	this.zadbotReason = "conquest";
	this.zadbotLimit = null;
	for (const name of InitAttributes.settings.VictoryConditions || [])
	{
		const match = /^zadbot_limit_(\d+)$/.exec(name);
		if (match)
			this.zadbotLimit = +match[1];
	}

	this.ZadbotPrint("start", {
		"limit": this.zadbotLimit,
		"victoryConditions": InitAttributes.settings.VictoryConditions || [],
		"mapName": InitAttributes.settings.mapName || InitAttributes.map || "",
		"seed": InitAttributes.settings.Seed ?? null,
		"aiSeed": InitAttributes.settings.AISeed ?? null,
		"players": this.ZadbotAllPlayers()
	});

	this.DoRepeatedly(60 * 1000, "ZadbotTick", {});
	if (this.zadbotLimit)
		this.DoAfterDelay(this.zadbotLimit * 60 * 1000, "ZadbotTimeLimit", {});
};

Trigger.prototype.ZadbotTick = function()
{
	if (!this.zadbotEnded)
		this.ZadbotPrint("tick", { "players": this.ZadbotAllPlayers() });
};

Trigger.prototype.ZadbotTimeLimit = function()
{
	const cmpPlayerManager = Engine.QueryInterface(SYSTEM_ENTITY, IID_PlayerManager);
	const active = cmpPlayerManager.GetActivePlayers();
	if (this.zadbotEnded || !active.length)
		return;

	let best = -Infinity;
	let winners = [];
	for (const playerID of active)
	{
		const score = this.ZadbotPlayerStats(playerID).score;
		if (score > best)
		{
			best = score;
			winners = [playerID];
		}
		else if (score == best)
			winners.push(playerID);
	}

	this.zadbotReason = "time_limit";
	Engine.QueryInterface(SYSTEM_ENTITY, IID_EndGameManager).MarkPlayersAsWon(
		winners,
		n => "Time limit: highest score wins.",
		n => "Time limit: lower score.");
};

/**
 * Runs on every win and defeat. The match is over once no player is active
 * any more; only then are all winners and losers known.
 */
Trigger.prototype.ZadbotPlayerStateChanged = function(data)
{
	if (this.zadbotEnded)
		return;

	const cmpPlayerManager = Engine.QueryInterface(SYSTEM_ENTITY, IID_PlayerManager);
	if (cmpPlayerManager.GetActivePlayers().length)
	{
		if (data && data.playerId !== undefined &&
			QueryPlayerIDInterface(data.playerId).GetState() == "defeated")
			this.ZadbotPrint("defeated", { "player": data.playerId });
		return;
	}

	this.zadbotEnded = true;
	const players = this.ZadbotAllPlayers();
	this.ZadbotPrint("end", {
		"reason": this.zadbotReason,
		"winners": players.filter(p => p.state == "won").map(p => p.id),
		"players": players
	});
};

{
	const cmpTrigger = Engine.QueryInterface(SYSTEM_ENTITY, IID_Trigger);
	cmpTrigger.RegisterTrigger("OnInitGame", "ZadbotInit", { "enabled": true });
	cmpTrigger.RegisterTrigger("OnPlayerWon", "ZadbotPlayerStateChanged", { "enabled": true });
	cmpTrigger.RegisterTrigger("OnPlayerDefeated", "ZadbotPlayerStateChanged", { "enabled": true });
}

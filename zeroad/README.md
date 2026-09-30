# zadbot — 0 A.D. learning bot (concept)

**Goal:** a bot that plays **complete 0 A.D. matches to the end** (conquest),
and **learns** to play better by playing against the game's own AI, Petra.

The bot is Petra itself, taken from the installed game, with about ten of its
settings learned: when to advance, how many workers, how eager to attack, and
so on. Every learned value is a readable "concept" of how to play, and the
bot always runs the Petra code of the exact game version you have.

```
 0 A.D. 0.28.0 (headless, no window)                 zadbot (Python)
 ┌──────────────────────────────────────┐           ┌───────────────────────────────────┐
 │ player 1: zadbot = Petra + learned   │  settings │ cross-entropy search:             │
 │           settings (params.js)       │ ◀──────── │  try 8 variations of the settings │
 │ player 2: plain Petra                │           │  play each vs Petra (same seeds)  │
 │ ZadbotReport.js: stats, time limit   │ ────────▶ │  move towards the best ones       │
 └──────────────────────────────────────┘  replay:  │  repeat → learned settings        │
                                   winner, scores   └───────────────────────────────────┘
```

## Game and bot versions (checked)

| | version | status |
|---|---|---|
| 0 A.D. | **0.28.0** "Boiorix" (18 Feb 2026, latest release) | target version |
| Petra | ships inside 0 A.D., so always the same version as the game | used |
| Arch-AI ([eserlxl/Arch-AI](https://github.com/eserlxl/Arch-AI)) | supports only Alpha 23 (0ad-23-1, 23-2) | **not compatible** with 0.28.0, not used |

How version compatibility is enforced:

* `zadbot/mod/zadbot/mod.json` declares `"dependencies": ["0ad=0.28.0"]`.
  The game refuses the mod on any other version. In headless mode it stops
  with "Trying to start with incompatible mods".
* `zadbot check` reads the installed game's version and refuses to run on
  another one.
* The bot does not copy Petra's code. `simulation/ai/zadbot/learned.js`
  imports Petra's modules from the installed game (0.28.0 turned the AI code
  into JavaScript modules) and only changes values in Petra's `Config` after
  Petra has set it up for the match.
* `tests/test_js.py` runs the bot on the **real 0.28.0 Petra** under Node.js.
  It checks that every learned setting exists in that Petra and is applied as
  intended, and that a plain Petra in the same match stays untouched.

## Does the bot finish a game?

```powershell
zadbot finish-test --bot petra:5 --opponent petra:1 --games 2   # Very Hard vs Very Easy
zadbot finish-test --bot petra:3 --opponent petra:3 --games 2   # equal strength
zadbot finish-test --bot zadbot:3 --opponent petra:3            # after learning (install-mod first)
```

Each game runs headless with no time limit until one side has conquered the
other. The game exits by itself when a player has won. The report shows who
won, the game length, and where the replay is; you can watch it in the game
under *Replays*.

> **Not run yet on the real game.** The game could not run in the
> environment this was built in: `pyrogenesis` refuses to run as root, and no
> other user was available. Everything else was tested (see *Status*).
> Run the commands above on your PC. Please share the output, so the numbers
> can go into this README.

## How the learning works

1. **Scenarios.** Each generation picks maps, seeds and civilisations (mirror
   match by default), and which side the bot plays. Every candidate plays the
   same scenarios. A match with the same seeds and settings plays out the same
   way, so candidates are compared on equal terms.
2. **Candidates.** The current centre (the bot as learned so far), plus 7
   random variations around it.
3. **Matches.** Each candidate plays its scenarios against plain Petra at the
   same difficulty (default Medium, which gives no AI bonus). A match ends by
   conquest, or after `time_limit` minutes (default 30), when the higher
   score wins. The score is the same total as on the game's summary screen.
4. **Fitness** per match: **+1** conquest win, **−1** conquest loss. When the
   time limit decides, the score margin counts, capped at ±0.5, so finishing
   the game is worth more.
5. **Update.** The centre moves towards the 3 best candidates, and the spread
   shrinks to how much they differ.

The centre is the learned bot. `zadbot evaluate` plays it against Petra on
seeds it never trained on, next to plain Petra vs Petra on the same seeds
(`--baseline`).

### The concepts it learns

| setting | Petra's value it changes | meaning |
|---|---|---|
| `aggressive` | `personality.aggressive` | eagerness to attack; above 0.7 Petra plans early rushes |
| `defensive` | `personality.defensive` | towers, fortresses, bigger defence |
| `phase2_pop` | × `Economy.popPhase2` | population before the town phase |
| `phase3_workers` | × `Economy.workPhase3` | workers before the city phase |
| `workers` | × `Economy.targetNumWorkers` | target number of workers |
| `support_ratio` | × `Economy.supportRatio` | share of women among the workers |
| `barracks1_pop` / `barracks2_pop` | × `Military.popForBarracks1/2` | population when barracks are built |
| `soldier_priority` | × `priorities.citizenSoldier` | resources for citizen soldiers |
| `military_building_priority` | × `priorities.militaryBuilding` | resources for military buildings |

"×" settings multiply what Petra itself chose for this match (difficulty,
map, population cap), so 1.0 is plain Petra. `zadbot show` lists the learned
values in these words.

## Setup on your PC (Windows)

### 1. The game

Install **0 A.D. 0.28.0** from <https://play0ad.com/download/>
(`0ad-0.28.0-win64.exe`). By default it goes to
`%LOCALAPPDATA%\0 A.D. Empires Ascendant`, where zadbot finds it.

### 2. Get the code

With git (`git --version` works):

```powershell
cd D:\
git clone -b claude/wizardly-volta-4clefo https://github.com/suntwosurf/suntwosurf.git zadbot-src
```

Without git: download
<https://github.com/suntwosurf/suntwosurf/archive/refs/heads/claude/wizardly-volta-4clefo.zip>
(logged in to GitHub), unzip it to `D:\zadbot-src`, and use the folder that
contains `zeroad` below.

### 3. Python

```powershell
conda create -n zadbot python=3.11 -y
conda activate zadbot
cd D:\zadbot-src\zeroad
pip install -e .
zadbot check            # game path, version 0.28.0, Petra found
zadbot install-mod      # copies the mod to Documents\My Games\0ad\mods\zadbot
```

Run every `zadbot` command from the `zeroad` folder: results go to `runs\`
in the current folder (never run it from `C:\WINDOWS\system32`).
`zadbot` only exists inside the `zadbot` environment, so run
`conda activate zadbot` in every new window.

If `check` does not find the game, pass `--game "D:\Games\0 A.D. Empires Ascendant"`
(or the full path to `pyrogenesis.exe`), or set `[game] path` in a copy of
`configs\default.toml`.

### 4. Finish test, learn, evaluate

```powershell
zadbot finish-test --games 2                       # does it finish full games?
zadbot learn --generations 10 --workers 3          # 8 candidates x 2 matches per generation
zadbot show                                        # learning curve + learned settings
zadbot evaluate --baseline --workers 3             # learned vs Petra, and Petra vs Petra
zadbot install-mod --remove-slots                  # put the learned bot into the game
```

`learn` saves after every generation (`runs\learner.json`). `Ctrl+C` stops it,
and running it again resumes. Every match is saved in `runs\matches\` together
with its replay folder.

Run time: a headless match runs much faster than real time, but Petra's AI is
CPU-heavy. Run one `finish-test` first to see how long a match takes on your
PC. With `--workers 3`, a generation is 16 matches.

### 5. Play against it

In the game: *Settings → Mod Selection*, enable **zadbot**, start. In the
match setup, pick **zadbot (learned Petra)** as an AI player. Difficulty
levels work as for Petra.

## Configuration

`configs/default.toml` lists every setting with its default. Use it with
`--config`. The main ones:

| key | default | |
|---|---|---|
| `[game] workers` | 2 | headless games at a time (about 1 CPU core each) |
| `[match] maps`, `size` | mainland, 128 | small maps give short games |
| `[match] difficulty` | 3 | both players; 3 = Medium, no AI bonus |
| `[match] time_limit` | 30 | minutes, then the score decides; 0 = until conquest |
| `[learn] population`, `matches_per_candidate` | 8, 2 | matches per generation = product |

## Without the game

`--game fake` uses a stand-in (`zadbot/fakegame.py`). It takes the same
command line, checks the mod version the same way, and writes the same
replay files, but the matches are pretend: a hidden "best" setting decides
who wins. Use it to try the commands; it says nothing about 0 A.D.

```powershell
zadbot finish-test --game fake
zadbot learn --game fake --generations 8 --workers 4
zadbot evaluate --game fake --baseline
```

## Tests

```powershell
pip install -e ".[dev]"
pytest                                   # needs Node.js for the JavaScript tests
$env:ZADBOT_GAME = "C:\...\pyrogenesis.exe"; pytest tests/test_js.py   # + checks against your game's Petra
```

## Layout

```
zadbot/
  mod/zadbot/          the 0 A.D. mod (copied by install-mod)
    mod.json                               depends on 0ad=0.28.0
    simulation/ai/zadbot/learned.js        Petra + learned settings (no Petra code copied)
    simulation/ai/zadbot/_zadbot.js, params.js, data.json
    maps/scripts/ZadbotReport.js           per-minute stats, time limit, end result
    simulation/data/settings/victory_conditions/zadbot_report.json
  game.py        find the game, its version, user data folder
  modinstall.py  install the mod, training slot AIs zadbot_t<n>, time-limit conditions
  params.py      the learned settings ("concepts") and their ranges
  match.py       run one headless match, read its replay (metadata.json)
  learner.py     cross-entropy search over the settings
  pipeline.py    parallel matches for learning, evaluation, finish test
  fakegame.py    stand-in game for trying without 0 A.D.
  cli.py         zadbot check | install-mod | match | finish-test | learn | evaluate | show
configs/default.toml
tests/           pytest; tests/js/ runs the mod's JavaScript under Node.js
```

## Status

Tested:
* The bot's JavaScript on the real 0.28.0 Petra (extracted from the official
  0.28.0 release), at difficulties 1, 3 and 5.
* `ZadbotReport.js` in a mocked simulation: per-minute stats, the time-limit
  decision including ties, one end line after conquest, and the summary-screen
  score formula.
* The whole Python pipeline against the fake game: headless run, replay
  parsing, parallel slots, learning, evaluation, resume, and refusing another
  game version. 44 tests.

Not tested yet (needs the game running):
* Real headless matches: the finish test and learning. The command line
  flags, replay files and trigger/AI APIs used here were read from the 0.28.0
  source and data, but have not been exercised in a running game.
* Match length and speed on real hardware, so the default `time_limit`,
  `workers` and generation sizes are first guesses.
* On Windows the game sends its console text to the debugger rather than to
  the console, so results are read from the replay files, which the game
  writes on every OS. Live progress lines appear only on Linux/macOS.

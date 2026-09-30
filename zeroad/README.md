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

Result on Windows, 0 A.D. 0.28.0, `random/mainland` size 128, seed 1:

| bot | opponent | result | game time | real time |
|---|---|---|---|---|
| Petra Very Hard (athen) | Petra Very Easy (sele) | **won by conquest**, score 3591 vs 1147 | 12.0 min | ~6 s |
| Petra Medium (cart) | Petra Medium (germ) | lost by conquest, 3058 vs 4753 | 26.1 min | ~6 s |
| Petra Medium (iber) | Petra Medium (mace) | **won by conquest**, 13205 vs 6416 | 36.6 min | ~18 s |

Every game was played to the end. A headless game runs about 100 times
faster than real time here: a learning match (30 min limit) took 6–25 s,
so a generation of 16 matches takes about 4 minutes with one game at a time.

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

Reading `zadbot show`: a single match is close to a coin flip, so the
centre's value for one generation (4 matches) swings a lot, and "best" is the
luckiest of 16 candidates, high even when nothing is learned. Watch `avg5`,
the centre's average over 5 generations, and trust `evaluate`.

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
zadbot learn --generations 10                      # 8 candidates x 2 matches per generation
zadbot show                                        # learning curve + learned settings
zadbot evaluate --baseline                         # learned vs Petra, and Petra vs Petra
zadbot install-mod --remove-slots                  # put the learned bot into the game
```

`learn` saves after every generation (`runs\learner.json`). `Ctrl+C` stops it
(and the game it is running), and running it again resumes. Every match is
saved in `runs\matches\` together with its replay folder.

**Parallel games on Windows.** 0 A.D. opens its game files on Windows so
that no other program can read them while it runs; a second copy of the game
then misses `mod.zip` or `public.zip` and fails. So:

* `--workers 1`: zadbot uses your installed game and refuses to start while
  0 A.D. is running. Don't play meanwhile.
* `--workers 2` or more: each worker gets its **own copy of the game**
  (`runs\game-copies\worker<n>`, about 4.2 GB each, made once and reused)
  and runs it with `-writableRoot`, which keeps that copy's mods, replays,
  config and logs inside it. The workers never touch your normal 0 A.D.
  folders, so you can even play meanwhile. Put the copies on a bigger drive
  with `--copies-dir`.

If games from an earlier run are left over, zadbot says so. End them with:

```powershell
Stop-Process -Name pyrogenesis -Force
```

### Faster on a big PC

Training time is almost all game time, and games run on the CPU only (the
simulation and Petra's AI; the GPU is not used in headless mode). So more CPU
cores means more games at once. `configs/many_cores.toml` is set up for about
20 cores: 16 games at a time and 64 matches per generation (4 per candidate).

More matches per candidate matter: in the first real learning run (2 matches
per candidate), candidates that differed only slightly from Petra won or lost
the same map and seed by chance, so the pick of the "best" candidate was
mostly luck.

```powershell
zadbot learn --config configs\many_cores.toml --generations 30
zadbot evaluate --config configs\many_cores.toml --baseline --matches 48
```

It needs about 67 GB of disk for the 16 copies. With less disk, lower
`workers` in the file (each worker costs about 4.2 GB).

Run time: a headless match runs much faster than real time, but Petra's AI is
CPU-heavy. Run one `finish-test` first to see how long a match takes on your
PC. A generation is 16 matches.

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
| `[game] copies_dir` | `runs/game-copies` | Windows, 2+ workers: one game copy per worker (~4.2 GB each) |
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
  game version. 64 tests.
* A real headless match on Windows with 0.28.0 (`finish-test`, see above):
  started by zadbot, played to conquest, and read back from the replay.

Not tested yet:
* Whether the learned bot beats Petra: learning runs on the real game
  (Windows, 16 parallel copies, 64 matches per generation, about 0.3-1.3 min
  real time per match with 16 at once), but its result is not evaluated yet.
* On Windows the game sends its console text to the debugger rather than to
  the console, so results are read from the replay files, which the game
  writes on every OS, and errors from the game's own log
  (`%LOCALAPPDATA%\0ad\logs\interestinglog_<time>_<pid>.html`). Live
  progress lines appear only on Linux/macOS.

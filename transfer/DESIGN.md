# Concept transfer: 0 A.D. → OpenRA (Red Alert)

**Goal:** learn, in 0 A.D., a representation of RTS play whose concepts
survive a change of game. The test is not "a strong 0 A.D. bot". It is:
**does a student trained in 0 A.D. learn OpenRA with much less OpenRA
experience than the same student trained on OpenRA alone?**

Status: 1000 recorded Petra games; the imitation students (with and without
concepts) are trained and scored on unseen games (*First results*). Not yet
playing in either game. A working headless OpenRA setup (`openra/setup.sh`).

## Versions (checked)

| | version | notes |
|---|---|---|
| 0 A.D. | **0.28.0** | as `zeroad/`. The only AI it ships is **Petra** (`simulation/ai/` holds `petra` and `common-api`). |
| Hannibal | not usable | A third-party 0 A.D. bot from the Alpha 18–20 era (SpiderMonkey 29/31). It is not part of 0.28.0 and would need a port to 0.28.0's modules. Petra stays the teacher. |
| OpenRA-RL | **0.4.1**, [yxc20089/OpenRA-RL](https://github.com/yxc20089/OpenRA-RL) @ `5dadd44` (2026-04-22) | Python/gRPC interface for AI agents: observations, 21 command types, headless mode, fast-forward, many games in one process. GPL-3.0. |
| its engine | [yxc20089/OpenRA](https://github.com/yxc20089/OpenRA) @ `9b271c1` (2026-03-25) | A fork: OpenRA's development branch of 2026-01-27 (`ef5541c`) plus 51 RL commits. Upstream's `playtest-20260222` has 11 more commits; the stable `release-20250330` is older. **The RL interface exists only in this fork**, so this is the OpenRA version we pin. |
| Red Alert content | not needed | Headless games need only the rules and maps in the engine. |

`openra/setup.sh` builds exactly these commits (checked in this repository's
container: build, then one game against OpenRA's built-in AI).

## Why the current zadbot can't be transferred

zadbot learned **10 numbers in Petra's config**, fixed for the whole game. It
never looks at the game state, and the numbers mean nothing outside Petra. It
also did not beat Petra: 53 % vs 50 % on 200 fresh maps (`zeroad/README.md`).
The harness (headless matches, parallel copies, fair paired evaluation) is
reused. The learner is replaced by a student that looks at the game state.

## Architecture

```
 game state ──▶ encoder (per game) ──▶ shared features ──▶ concepts ──▶ policy ──▶ macro action ──▶ executor (per game) ──▶ game commands
               0 A.D.: JS in the bot     21 numbers        ~7 numbers    shared      how many of      0 A.D.: Petra's machinery
               OpenRA: Python            (same meaning      (bottleneck,  core        each of 10       OpenRA: scripted, Python
                                          in both games)     supervised)
```

* **Decision step:** every 10 s of game time. The student decides the macro
  actions for the next 10 s and how many of each: say 3 workers, a house and an
  attack. Moment-to-moment
  control (placing buildings, sending gatherers, fighting) stays with a
  scripted executor in each game.
* **Shared core** (concepts + policy) is trained in 0 A.D. In OpenRA it is
  reused: frozen, then fine-tuned.
* **Encoder and executor** are written by hand for each game. That's where
  the games differ; the core only sees the shared features.

So what can transfer is the *strategy over shared concepts*: when to grow the
economy, when production is the bottleneck, when to defend, when an attack
pays off. Unit micro and build placement do not transfer; they are the
executors' job.

### Shared features (every decision step)

| feature | 0 A.D. | OpenRA |
|---|---|---|
| game time | minutes | ticks / 25 / 60 |
| stockpile | food+wood+stone+metal, in units of a soldier's cost | cash + ore, in units of a rifleman's cost |
| income, spending | gathered / used in the last minute | cash + ore change / spending in the last minute |
| gatherers | units gathering | harvesters |
| economy buildings | dropsites, farms, fields | refineries, silos |
| supply headroom | 1 − pop / pop cap | power surplus / power produced |
| production buildings | barracks, stables, ranges, civic centres | barracks, war factories, airfields, naval yards |
| production busy | share of production queues in use | share of production queues in use |
| army size, army value | own soldiers (count, cost) | own combat units (count, cost) |
| defences | towers, walls, fortresses | pillboxes, turrets, AA, tesla coils |
| tech level | phase 1–3 → 0, 0.5, 1 | none / radar / tech centre → 0, 0.5, 1 |
| enemy seen | visible enemy army value, buildings | visible enemy army value, buildings |
| recent fighting | value killed / lost in the last minute | kills_cost / deaths_cost change |
| threat at home | enemy army value near own buildings | same |
| map explored | % explored | % explored |

Values are normalised per game (costs, typical income), so the numbers mean
the same thing in both.

### Memory (the student's own decisions)

Petra works in rhythms: an attack every few minutes, one phase at a time.
So the student also sees what it decided lately (`memory.py`): per action,
in how many of the last 6 steps it took it, and how many steps ago it last
took it (up to 30). It keeps this memory itself in either game, so it needs
nothing from the encoders. One class builds it from recordings for training
and step by step in play, so the two can't differ.

### Concepts (the bottleneck)

Each concept is a number 0..1, computed by a fixed formula (`concepts.py`)
from the features and from information the student doesn't see: the
teacher's full view in recorded games and its recent decisions. The student
learns to predict them, and the policy only sees the predicted concepts, the
memory and a few raw features (game time, tech, bases). That makes the
concepts explicit, checkable and shared.

"Capacity" is what the production buildings and bases spend per minute when
busy: 5 (money / 100) each, the median in the 0 A.D. recordings.

| concept | label |
|---|---|
| RESOURCE_SCARCITY | 1 − (stock + income) / 2 minutes of capacity |
| PRODUCTION_BOTTLENECK | stock beyond 1 minute of capacity; full at 3 |
| SUPPLY_BLOCK | 1 − supply headroom / 10 % |
| ECONOMIC_INVESTMENT | economy's share of the last minute's decisions, by rough cost (workers, economy buildings, new bases vs army, production, defences) |
| MILITARY_ADVANTAGE | own army value / (own + the enemy's true value) |
| ECONOMIC_ADVANTAGE | own income / (own + the enemy's true income) |
| DEFENSIVE_PRESSURE | (enemy army at home + losses) / (that + own army), while enemies are at home |

EXPANSION_OPPORTUNITY (own nearby resources running low while the map is
quiet) is left out for now: the recorder doesn't log the resources left near
the bases. ECONOMIC_ADVANTAGE uses the recorded true enemy income instead.

### Macro actions

A step holds a count for each action (none at all is a valid step).

| action | 0 A.D. (via Petra's queues and plans) | OpenRA (scripted executor) |
|---|---|---|
| TRAIN_WORKER | women / gatherers | harvester |
| TRAIN_ARMY | soldiers | infantry / vehicles |
| BUILD_SUPPLY | house | power plant |
| BUILD_ECONOMY | storehouse, farmstead, field | refinery, silo |
| BUILD_PRODUCTION | barracks, stable, range | barracks, war factory |
| BUILD_DEFENSE | tower, wall, fortress | pillbox, turret |
| TECH_UP | next phase | radar dome, tech centre |
| EXPAND | new civic centre | MCV → new base |
| ATTACK | launch an attack plan | attack-move the army |
| DEFEND | pull the army home | pull the army home |

Known mismatches: in 0 A.D. citizen soldiers both gather and fight; OpenRA
has no builder units (the construction yard builds); supply is population in
0 A.D. and power in OpenRA; tech is phases vs buildings; 0 A.D. has four
resources, OpenRA one. The encoders absorb these, and the transfer result
shows whether that is enough.

## Teacher data (0 A.D.)

Petra vs Petra, recorded in the game:
* The student's encoder runs inside a recording wrapper around an
  unchanged Petra. Every 10 s it writes the shared features and the concept
  labels with `log()`. That lands in `mainlog_<time>_<pid>.html` on every OS,
  including Windows.
* The wrapper watches what Petra queues and launches (training and
  construction plans, attack plans) and classifies it into macro actions: the
  label the student imitates.
* **Coverage over volume:** vary Petra's difficulty (2–5) and behaviour
  (aggressive / balanced / defensive), civilisations, maps and map sizes,
  starting resources. Also vary the rules through a trigger script: resources
  ±30 %, gather rates ±20 %.
* **Volume:** start at about 10 000 games and grow while the validation
  curve still improves. On the user's PC (16 games at once, 0.3–1.3 min
  real time per game) that is about half a day to a day.

## Training in 0 A.D.

1. **Behaviour cloning (BC):** predict Petra's macro action, and the concept
   labels, from the features.
2. **Playing:** the student runs inside the game (the small network's weights
   in a generated JS file, like `params.js` now). Its macro actions drive
   Petra's machinery.
3. **Improvement:** BC at best matches its teacher (about 50 % vs Petra).
   Beating Petra needs reinforcement learning on game outcomes, starting from
   the BC student.

## Transfer gates (before OpenRA)

| gate | target | measured by |
|---|---|---|
| BC macro-action AUC | ≥ 0.9 mean on held-out Petra games, rare decisions included | recorded games not used in training |
| vs Petra, training maps | 70–80 % | `evaluate`, map by map |
| vs Petra, unseen maps and seeds | 60–70 % | `evaluate --seed`, with the 95 % range |
| vs Petra Very Hard (5) | within 10–15 points of Petra's own result vs Petra 5 | the same matches with Petra 3 in the student's place |
| perturbed rules (resources, gather rates, aggression) | holds most of its win rate | `evaluate` under each perturbation |
| catastrophic failures | rare | share of games lost early or with the economy stalled |

Stop when these hold: competent, not specialised on Petra. The win-rate gates
need the reinforcement-learning step; BC alone is not expected to reach them.

The first gate used to be "≥ 90 % agreement". With rare decisions, "always
say no" already agrees 89 % of the time on average (99 % on TECH_UP), so it
is now stated as AUC, which doesn't depend on a cut-off.

## First results (1000 games, 0 A.D. 0.28.0)

`zadbot record` on Windows, 16 games at a time: 1000 Petra vs Petra games
(difficulty 2-5, every behaviour), 354 350 steps, median game 28 game-min,
906 ended by conquest. Every feature is in range. Petra's own win rate
rises with difficulty (18 % at 2 to 78 % at 5), as it should.

Trained on 800 games and scored on the other 200 (`rtsconcepts bc`,
`student --compare`, `curve`):

| student | mean AUC | AUC on rare decisions (< 5 % of steps) | skill vs base rate |
|---|---|---|---|
| C: features only | 0.851 | 0.865 | 23 % |
| C: features + memory | **0.893** | **0.926** | **33 %** |
| A/B core: features + memory → concepts → decisions | 0.875 | 0.906 | 29 % |

* The first report of `bc` said "no better than guessing" on every rare
  decision (balanced accuracy 50 %). That was its 50 % cut-off: the model
  never gave a rare decision more than 50 %, yet it ranked the right moments
  first (AUC 0.81-0.91 without memory).
* The memory adds most on the rhythmic decisions: ATTACK 0.88 → 0.94,
  TECH_UP 0.91 → 0.97, BUILD_PRODUCTION 0.83 → 0.90, BUILD_DEFENSE
  0.81 → 0.88. A bigger network adds almost nothing (0.856 with 3 × 128
  units, features only).
* The concepts are predicted closely on unseen games: 95-99 % of the
  variation explained for those that follow from the features and the
  memory, 88 % for the two that need the enemy's true army and income. The
  bottleneck costs 0.02 AUC. Whether it pays for that in OpenRA is what the
  A/B/C experiment measures.
* BUILD_ECONOMY is the hardest decision (AUC 0.77-0.79): the features don't
  say which resources run short or how far the gatherers walk.
* Learning curve (features + memory): 50 games 0.827, 100 0.848, 200 0.871,
  400 0.883, 800 0.893. Still rising, mostly on the rare decisions: record
  more.

## Transfer experiment (OpenRA)

Every student gets the **same OpenRA budget** of G games against OpenRA's
built-in AI (tiers beginner … brutal; styles rush, normal, turtle), for
G = 0, 50, 200, 1000:

| student | start | OpenRA budget used for |
|---|---|---|
| **A: transfer** | shared core from 0 A.D.; only the OpenRA encoder's normalisation fitted | fine-tuning |
| **B: from scratch** | same architecture, random start | all training |
| **C: no concepts** | 0 A.D.-trained, but features → actions without the concept bottleneck | fine-tuning |

Plus two reference points: random macro actions, and a hand-written rule
policy on the same executor.

OpenRA's built-in bots draw their random choices from the clock, so the same
map and seed don't replay the same game. Comparisons therefore need enough
games per budget for their 95 % ranges to separate, not pairing by seed as
in 0 A.D.

**Result:** win rate against budget G. Transfer shows as A above B at small
budgets: at G = 0 (zero-shot) and in the area under the curve. A vs C shows
whether the concept layer is what transfers.

## Milestones

1. **Done:** OpenRA-RL runs headless in the container (pinned versions,
   `openra/setup.sh`). Also confirmed that 0 A.D. bots can write data to the
   log on every OS.
2. **Done:** the shared features and macro actions in code: the Python
   definition (`rtsconcepts/spec.py`), the 0 A.D. encoder in JS
   (`zeroad/.../ai/zadbot/rts.js`, kept equal by a test) and the OpenRA
   encoder (`rtsconcepts/openra.py`, from OpenRA-RL's `get_game_state`).
3. **Done, more to come:** the AI "zadbot recorder (plain Petra)" and
   `zadbot record`, checked on the real 0.28.0 Petra modules with a made-up
   game state and end to end on the stand-in game; `rtsconcepts/dataset.py`
   loads the recordings. 1000 real games recorded (*First results*). The
   learning curve still rises: record more (`--seed 2`, ...).
4. **Imitation done, playing next:** `rtsconcepts inspect` (value ranges,
   decision rates, concept labels), `bc` (student C), `student` (the concept
   core of A and B) and `curve`, all scored on unseen games without a
   cut-off. The memory of own decisions is part of both students.
   Next: the student playing in 0 A.D.: its weights in a generated JS file,
   its decisions driving Petra's queues and managers.
5. Evaluation vs Petra; reinforcement learning to pass the gates.
6. **Started:** the OpenRA executor (`rtsconcepts/openra.py`: each macro
   action as OpenRA-RL tool calls, one structure at a time, attacks with a
   memory of enemy sightings and a map sweep) and a game driver
   (`openra_play.py`). A 30-game-minute game takes 20-45 s of real time.
   Found on the way:
   * The pinned engine crashed headless when a GPS satellite launched (a
     screen effect without a screen). `openra/patches/` fixes it and
     `setup.sh` applies it.
   * OpenRA-RL's default map (Singles) is mostly water: a land army can't
     reach every enemy building, so games never end. Land maps are needed
     (e.g. Ore Lord). The "beginner" AI doesn't build on Ore Lord; easy,
     medium and normal do, and rush early.
   * A hand-written rule policy on this executor loses to OpenRA's easy AI on
     Ore Lord. So win/loss alone would show no difference between students.
   * `policies.BCPolicy` plays either trained student: it keeps the memory
     of its own decisions and, by default, takes each action with its
     predicted probability, so rare decisions happen about as often as
     Petra made them. (With a 50 % cut-off, the memory model would attack
     in 0.2 % of steps instead of Petra's 1.6 %, and phase up in 0.05 %
     instead of 0.7 %.)

   Next: a continuous outcome as in 0 A.D. (win +1, loss −1, and at the time
   limit the value killed minus lost, within ±0.5); a ladder of opponents
   (a passive "dummy" AI, beginner on a map where it works, easy, ...); then
   the A/B/C experiment.

## What runs where

* **0 A.D.:** on the user's Windows PC (the game refuses to run as root, which
  is how this container runs).
* **OpenRA:** headless in this container, and on the user's PC (Docker, or
  `openra/setup.sh` in WSL2).
* **Training the student:** either; the RTX 3090 helps here.

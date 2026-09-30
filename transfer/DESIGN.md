# Concept transfer: 0 A.D. → OpenRA (Red Alert)

**Goal:** learn, in 0 A.D., a representation of RTS play whose concepts
survive a change of game. The test is not "a strong 0 A.D. bot". It is:
**does a student trained in 0 A.D. learn OpenRA with much less OpenRA
experience than the same student trained on OpenRA alone?**

Status: design, plus a working headless OpenRA setup (`openra/setup.sh`).
Nothing below has been trained yet.

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

### Concepts (the bottleneck)

Each concept is a number 0..1, computed by a fixed formula from the features
and from information the student doesn't see: the teacher's full view in
recorded games. The student learns to predict them, and the policy only sees
the predicted concepts plus a few raw features. That makes the concepts
explicit, checkable and shared.

| concept | meaning (label) |
|---|---|
| RESOURCE_SCARCITY | stockpile and income low compared with what production could spend |
| PRODUCTION_BOTTLENECK | money piling up faster than production buildings can spend it |
| SUPPLY_BLOCK | supply headroom near 0 (houses / power) |
| ECONOMIC_INVESTMENT | share of recent spending on the economy |
| MILITARY_ADVANTAGE | own army value vs the enemy's (the true value, from the recording) |
| DEFENSIVE_PRESSURE | enemy army near own buildings, recent losses at home |
| EXPANSION_OPPORTUNITY | own nearby resources running low while the map is quiet |

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
| BC macro-action agreement | ≥ 90 % on held-out Petra games, familiar situations | recorded games not used in training |
| vs Petra, training maps | 70–80 % | `evaluate`, map by map |
| vs Petra, unseen maps and seeds | 60–70 % | `evaluate --seed`, with the 95 % range |
| vs Petra Very Hard (5) | within 10–15 points of Petra's own result vs Petra 5 | the same matches with Petra 3 in the student's place |
| perturbed rules (resources, gather rates, aggression) | holds most of its win rate | `evaluate` under each perturbation |
| catastrophic failures | rare | share of games lost early or with the economy stalled |

Stop when these hold: competent, not specialised on Petra. The win-rate gates
need the reinforcement-learning step; BC alone is not expected to reach them.

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
2. **Done for 0 A.D.:** the shared features and macro actions in code: the
   Python definition (`rtsconcepts/spec.py`) and the 0 A.D. encoder in JS
   (`zeroad/.../ai/zadbot/rts.js`), kept equal by a test. Next: the OpenRA
   encoder in Python.
3. **Recorder done, data pending:** the AI "zadbot recorder (plain Petra)"
   and `zadbot record`. It is checked on the real 0.28.0 Petra modules with a
   made-up game state, and end to end on the stand-in game;
   `rtsconcepts/dataset.py` loads the recordings. The first 2 real games
   recorded fine: 666 steps, every kind of decision, no errors, and 0.3 min
   of real time per game. Next: thousands of games.
4. **Started:** `rtsconcepts inspect` (value ranges, decision rates) and
   `rtsconcepts bc`, the imitation baseline without concepts (student C). It
   is scored on unseen games next to "always the majority answer", with
   balanced accuracy as the fair measure. Next: the concept bottleneck, and
   the student playing in 0 A.D.
5. Evaluation vs Petra; reinforcement learning to pass the gates.
6. The OpenRA executor and the A/B/C experiment. It runs in this container,
   on OpenRA-RL, headless.

## What runs where

* **0 A.D.:** on the user's Windows PC (the game refuses to run as root, which
  is how this container runs).
* **OpenRA:** headless in this container, and on the user's PC (Docker, or
  `openra/setup.sh` in WSL2).
* **Training the student:** either; the RTX 3090 helps here.

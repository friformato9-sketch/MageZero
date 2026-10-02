# MageZero fork — Devourer of Truth

This fork backs the [Devourer of Truth](https://github.com/friformato9-sketch/mtg-judge-devourer-of-truth)
Telegram MTG judge bot. On top of upstream MageZero it adds:

- **Game simulations** (`mode: simulate`): play games between two decks without writing training
  data, validate decks against a format first, and stream each game turn by turn as JSON Lines.
  See [configs/simulate-modern.yml](configs/simulate-modern.yml).
- **Rules scenarios** (`mz-scenario`): rebuild an exact board state, replay scripted actions on the
  real rules engine and dump the result — used by the bot to check rulings.
- An XMage engine kept up to date with upstream XMage, so new cards are available.

The Java side lives in the engine fork
[friformato9-sketch/mage](https://github.com/friformato9-sketch/mage), branch `feature/telegram-sim`
(modules `Mage.MageZero` and `Mage.MageZero.Scenario`). This repo only holds the Python
pipeline, configs and decks; `xmage/` is the built distribution and is not versioned.

## Setup on Windows

Requirements: JDK 21 (Temurin), Maven 3.9, Python 3.10+.

```bash
# Python side
python -m venv .venv
.venv/Scripts/python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python -m pip install -e .

# Engine: build the fork and unpack the distribution into ./xmage
git clone https://github.com/friformato9-sketch/mage ../mage
cd ../mage && git checkout feature/telegram-sim
mvn install -DskipTests -T 1C -pl Mage.MageZero,Mage.Tests,Mage.MageZero.Scenario -am
cd ../magezero
unzip ../mage/Mage.MageZero/target/mage-magezero.zip "lib/*" -d xmage   # plus the files below
cp ../mage/Mage.MageZero/release/mz-*.bat ../mage/Mage.MageZero/release/mz-*.sh xmage/
cp ../mage/Mage.MageZero.Scenario/target/mage-magezero-scenario.jar \
   ../mage/Mage.MageZero.Scenario/target/scenario-lib/*.jar xmage/lib/
```

`xmage/` also needs `config/`, `decks/` and `log4j.properties` from the
[v0.2.0-alpha release](https://github.com/WillWroble/MageZero/releases) distribution. The card
database (`xmage/db/cards.h2.mv.db`) is created on first run; delete `xmage/db/` after installing
a new engine so it is rebuilt with the new cards.

> **Build in a folder your IDE does not have open.** The VS Code Java extension compiles opened
> Maven projects into the same `target/classes` directories and can wipe them halfway through a
> Maven build, producing jars with missing classes (e.g. `ClassNotFoundException:
> mage.cards.a.ArclightPhoenix` at startup). Use a separate worktree for builds:
> `git worktree add --detach ../mage-build <commit>` and run Maven there.

## Usage

```bash
# one Modern game, turn-by-turn log in .mz_tmp/simulate/
mz batch --config configs/simulate-modern.yml

# rules scenario (JSON spec documented in ScenarioRunner, examples in Mage.MageZero.Scenario/examples)
xmage/mz-scenario.bat <scenario.json> <out-dir>

# play quality: N games between two decks + blunder counts (missed land drops, pumps without attacks…);
# --jvm-opts passes engine switches such as -Dmagezero.eval.handCardScore=5 for A/B comparisons
mz bench --deck-a decks/modern/Modern-Prowess.txt --deck-b decks/modern/Modern-Goryos.txt --games 20 --threads 4 --out .mz_tmp/bench/x
```

In a game config, a minimax player block can add `advisor: {url: http://127.0.0.1:8765/advise}`: its key
decisions then go to the bot's play advisor (`advisor.py`, Claude or local qwen with the Modern
archetypes' game plans), which approves the engine's pick or asks it to search again with other weights.
The engine fork's `FORK.md` documents the protocol and the game log format.

## Training a deck while the bot runs

The bot uses its own copy of the distribution (`MAGEZERO_XMAGE_DIR`, e.g. `C:\Users\frifo\xmage-bot`),
so this repo's `xmage/` can be busy with training (Windows locks jars and the card DB in use).
Cap the training JVM's heap so both fit in RAM:

```powershell
$env:MZ_HEAP = '12g'
.venv\Scripts\mz train --run configs/run-prowess.yml --game configs/game-prowess.yml
```

`configs/run-prowess.yml` trains Modern Prowess against the local Modern pool (minimax opponents):
8 generations of 100 games per opponent. `configs/game-prowess.yml` lowers the MCTS budget
(800 sims / 2 s per decision) for this PC. `configs/run.yml` is a smaller Prowess run (4 games per
opponent per generation, against Modern-Goryos and the Standard pool); it starts from an existing model
(`start_from_version: 1`), so it needs `models/Modern-Prowess/ver1/model.pt.gz`. Without one, its
inference server fails at startup (as on 2026-10-02 17:59); set `start_from_version: null` to bootstrap.

`--resume` continues the active run in `runs/` from its current generation, but with the settings of
the `--run` file passed now (games per generation, opponents), not the ones recorded in its `run.json`.

### Known issues (2026-10-02)

- **The network does not fit in an 8 GB GPU.** `src/magezero/train.py` trains with a fixed batch of 512
  samples through a 512-wide transformer over each state's active features. On the RTX 4070 Laptop (8 GB)
  the first training step of `run-prowess.yml` failed with `CUDA error: out of memory`, after generation 0's
  300 games had completed. A quick probe also ran out of memory at batch 32, so the length of each state's
  feature list, not only the batch size, needs attention before training can run here. Unresolved.
- **Resuming after a failed train stage replays the generation.** Before training, the runner moves the
  generation's data from `data/<deck>/ver<v>/testing/` to `training/`, but on resume it counts completed
  games in `testing/`, so it finds none and plays them all again. To retry only the training, run it
  directly, e.g. `.venv\Scripts\python src/magezero/train.py --deck Modern-Prowess --version 1 --epochs 2`
  (2 epochs for the bootstrap generation; later generations use 1 epoch plus `--checkpoint`). Then set `current_gen` in the run's `run.json` to
  the next generation before `--resume`. This follows from reading `runner.py` and has not been tried yet.

## Modern metagame decks

`decks/modern/Modern-Meta-*.txt` are the representative lists of MTGGoldfish's top 15 Modern
archetypes (snapshot of 2026-10-02, all valid on the engine), for opponent pools and simulations.
They come from the bot repo's `metagame/modern/` (refreshed by its `scripts/update_metagame.py`),
which also holds a game plan per archetype for the play advisor. Copy new lists into `xmage/decks/`
too, where `mz` looks decks up by name. The older `Modern-Goryos`, `Modern-BorosEnergy`,
`Modern-Burn` and `Modern-Prowess` lists stay as they are (training runs use them).

## Updating the engine with new XMage cards

New cards exist only once XMage implements them in Java, so updating means merging upstream
XMage into the engine fork:

```bash
cd ../mage
git remote add xmage https://github.com/magefree/mage.git   # once
git fetch xmage master
git checkout -b merge/xmage-<version> feature/telegram-sim
git merge xmage/master        # conflicts are usually few: core files MageZero touches
# build in a separate worktree (see above), install into xmage/, delete xmage/db/,
# re-run configs/simulate-modern.yml and the scenario examples, then fast-forward
# feature/telegram-sim and push.
```

The 1.4.58 → 1.4.61 merge needed: the `Mage.Player.AI.MA` → `MAD` module rename,
`activatorId` removal in `AbilityImpl`, the soulbond API change (`getPairedMOR()`) in
`StateEncoder`, and the version bump of the MageZero modules.

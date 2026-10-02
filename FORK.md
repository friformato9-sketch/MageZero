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
```

## Training a deck while the bot runs

The bot uses its own copy of the distribution (`MAGEZERO_XMAGE_DIR`, e.g. `C:\Users\frifo\xmage-bot`),
so this repo's `xmage/` can be busy with training (Windows locks jars and the card DB in use).
Cap the training JVM's heap so both fit in RAM:

```powershell
$env:MZ_HEAP = '12g'
.venv\Scripts\mz train --run configs/run-prowess.yml --game configs/game-prowess.yml
```

`configs/run-prowess.yml` trains Modern Prowess against the local Modern pool (minimax opponents),
`configs/game-prowess.yml` lowers the MCTS budget (800 sims / 2 s per decision) for this PC.
`--resume` continues the active run in `runs/` with the configuration it was started with.

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

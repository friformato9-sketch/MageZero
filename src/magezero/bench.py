"""bench.py — measure AI play quality: N simulated games between two decks + obvious-blunder counts.

`mz bench --deck-a A.txt --deck-b B.txt --games 20 --threads 4 --out .mz_tmp/bench/x`

Runs the engine in `mode: simulate`, then reads every game_N.jsonl (see GameLogRecorder in the
engine fork) and counts, per player, mistakes no competent player makes:

- missed_land_drop   ended their own turn with a land in hand and no land played that turn
- discard_hand_size  discarded down to hand size
- pump_no_attack     targeted their own creature with an instant/sorcery on their own turn, the
                     creature did not attack, and no opponent spell or ability had targeted it
- self_counter       countered a spell or ability whose source they control

The JVM flags in `--jvm-opts` (e.g. `-Dmagezero.eval.handCardScore=5`) are passed through
JAVA_TOOL_OPTIONS, which makes A/B comparisons of engine settings possible.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

PLAYERS = ("PlayerA", "PlayerB")
BLUNDERS = ("missed_land_drop", "discard_hand_size", "pump_no_attack", "self_counter")

CAST_TARGETING = re.compile(r"^(PlayerA|PlayerB) casts (.+?) targeting (.+?)(?: from .*)?$")
COUNTERED = re.compile(r"^(?:Ability \(.*\) of )?(.+?) is countered by (.+)$")


def write_config(args, out: Path) -> Path:
    def player(deck: str) -> dict:
        return {
            "deckPath": str(Path(deck).resolve()),
            "type": args.ai,
            "output_file": "",
            "mcts": {"offline_mode": True, "search_budget": args.mcts_budget,
                     "timeout_ms": args.mcts_timeout_ms, "prune_duplicate_states": True},
            "gameplay": {"mulligans_enabled": args.mulligans, "manual_tapping": False},
        }
    config = {
        "mode": "simulate",
        "goes_first": "random",
        "game_mode": "normal",
        "player_a": player(args.deck_a),
        "player_b": player(args.deck_b),
        "training": {"games": args.games, "threads": args.threads,
                     "max_turns": args.max_turns, "max_minutes": args.max_minutes},
        "server": {"host": "localhost", "port": 50052, "opponent_port": 50053},
        "logging": {"save_final_wr": False, "log_feature_hash": False,
                    "game_log_dir": str(out.resolve())},
    }
    path = out / "bench.yml"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")  # JSON is valid YAML
    return path


def run_engine(config: Path, jvm_opts: str, log_path: Path) -> int:
    script = "xmage\\mz-xmage.bat" if sys.platform == "win32" else "xmage/mz-xmage.sh"
    cmd = ["cmd", "/c", script, str(config.resolve())] if sys.platform == "win32" else [script, str(config.resolve())]
    env = dict(os.environ)
    if jvm_opts:
        env["JAVA_TOOL_OPTIONS"] = jvm_opts
    with open(log_path, "wb") as log:
        return subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env).returncode


def analyze_game(path: Path) -> dict:
    """Blunder counts per player and the outcome of one game log."""
    blunders = {p: Counter() for p in PLAYERS}
    turn_logs: list[dict] = []
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        kind = event.get("type")
        if kind == "turn_start":
            turn_logs = []
        elif kind == "log" and event.get("active"):
            turn_logs.append(event)
        elif kind == "turn_end" and event.get("active"):
            _turn_blunders(event, turn_logs, blunders)
        elif kind == "game_end":
            result = {"winner": event.get("winner"), "reason": event.get("reason"), "turns": event.get("turns")}
    return {"blunders": {p: dict(c) for p, c in blunders.items()}, **result}


def _turn_blunders(turn_end: dict, logs: list[dict], blunders: dict) -> None:
    active = turn_end["active"]
    battlefield = {p: {c["name"]: c for c in cards} for p, cards in turn_end.get("battlefield", {}).items()}
    texts = [e.get("text", "") for e in logs]

    # missed land drop: a land stayed in hand and no land was played this turn
    hand = (turn_end.get("zones", {}).get(active) or {}).get("hand_cards", [])
    played_land = any(t.startswith(f"{active} plays ") for t in texts)
    if not played_land and any("land" in c.get("types", []) for c in hand):
        blunders[active]["missed_land_drop"] += 1

    targeted_by_opp: set[str] = set()
    attackers = {m.group(1) for t in texts if (m := re.match(r"^Attacker: (.+?) \(", t))}
    stack_casters: dict[str, str] = {}
    for event, text in zip(logs, texts):
        for player in PLAYERS:
            if text.startswith(f"{player} discards down to"):
                blunders[player]["discard_hand_size"] += 1
        cast = CAST_TARGETING.match(text)
        if cast:
            caster, spell, target = cast.groups()
            types = next((c.get("types", []) for c in event.get("cards", []) if c.get("name") == spell), [])
            own = battlefield.get(caster, {})
            if caster != active:
                targeted_by_opp.add(target)
            elif (target in own and "creature" in own[target].get("types", [])
                  and ("instant" in types or "sorcery" in types)
                  and target not in attackers and target not in targeted_by_opp):
                blunders[caster]["pump_no_attack"] += 1
            if target.startswith("stack ability") or target in ("spell",):
                stack_casters[spell] = caster
        countered = COUNTERED.match(text)
        if countered:
            source, by = countered.groups()
            caster = stack_casters.get(by)
            if caster and source in battlefield.get(caster, {}):
                blunders[caster]["self_counter"] += 1


def report(out: Path, label: str) -> dict:
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    games = sorted(out.glob("game_*.jsonl"))
    totals = {p: Counter() for p in PLAYERS}
    reasons = Counter()
    turns = []
    for g in games:
        r = analyze_game(g)
        for p in PLAYERS:
            totals[p].update(r["blunders"][p])
        reasons[r.get("reason")] += 1
        turns.append(r.get("turns") or 0)
    n = max(len(games), 1)
    result = {
        "label": label,
        "games": len(games),
        "wins_a": summary.get("wins_a"),
        "wins_b": summary.get("wins_b"),
        "draws": summary.get("draws"),
        "avg_turns": round(sum(turns) / n, 1),
        "end_reasons": dict(reasons),
        "blunders_per_game": {p: {b: round(totals[p][b] / n, 2) for b in BLUNDERS} for p in PLAYERS},
    }
    (out / "bench_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def print_report(r: dict, deck_a: str, deck_b: str) -> None:
    print(f"\n== {r['label']}: {r['games']} games, {deck_a} {r['wins_a']} - {r['wins_b']} {deck_b}"
          f" (draws {r['draws']}), avg turns {r['avg_turns']}, end reasons {r['end_reasons']}")
    print(f"{'blunders per game':<20}" + "".join(f"{b:>20}" for b in BLUNDERS))
    for p, name in zip(PLAYERS, (deck_a, deck_b)):
        print(f"{name:<20}" + "".join(f"{r['blunders_per_game'][p][b]:>20}" for b in BLUNDERS))


def cmd_bench(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.report_only:
        for old in out.glob("game_*.jsonl"):
            old.unlink()  # the engine numbers games from 1 again
        config = write_config(args, out)
        code = run_engine(config, args.jvm_opts, out / "engine.log")
        if code != 0:
            sys.exit(f"engine exited with {code}; see {out / 'engine.log'}")
    r = report(out, args.label or out.name)
    print_report(r, Path(args.deck_a).stem, Path(args.deck_b).stem)


class BoolArg(argparse.Action):
    """--mulligans true|false"""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=1, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values[0].lower() in ("1", "true", "yes", "on"))


def add_parser(sub) -> None:
    p = sub.add_parser("bench", help="play N games between two decks and count AI blunders")
    p.add_argument("--deck-a", required=True)
    p.add_argument("--deck-b", required=True)
    p.add_argument("--games", type=int, default=20)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--ai", choices=("minimax", "mcts"), default="minimax")
    p.add_argument("--mcts-budget", type=int, default=300)
    p.add_argument("--mcts-timeout-ms", type=int, default=3000)
    p.add_argument("--mulligans", action=BoolArg, default=True)
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--max-minutes", type=int, default=20)
    p.add_argument("--jvm-opts", default="", help="JVM flags, e.g. -Dmagezero.eval.handCardScore=5")
    p.add_argument("--out", required=True)
    p.add_argument("--label", default="")
    p.add_argument("--report-only", action="store_true", help="only re-analyze existing logs in --out")
    p.set_defaults(func=cmd_bench)

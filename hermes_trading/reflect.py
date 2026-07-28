#!/usr/bin/env python3
"""Reflection engine — fallback (deterministic) and hermes modes.

Every proposed change is backtested against recent history before it's
committed: reflection no longer mutates the live strategy blind."""
import argparse
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
import yaml

from hermes_trading.backtest import fetch_history, simulate, summarize, RSI_WINDOW

STATE_DIR = Path(__file__).parent.parent / "state"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"
HISTORY_DIR = STATE_DIR / "history"
HYPOTHESES_FILE = STATE_DIR / "hypotheses.jsonl"

def load_strategy():
    with open(STRATEGY_FILE) as f:
        return yaml.safe_load(f)

def save_strategy(s):
    with open(STRATEGY_FILE, "w") as f:
        yaml.safe_dump(s, f, sort_keys=False)

def propose_change(strategy):
    """Returns (candidate_strategy, hypothesis, changed) without touching
    the live strategy file."""
    candidate = copy.deepcopy(strategy)

    if strategy["entry"]["threshold"] > 25:
        candidate["entry"]["threshold"] -= 2
        hypothesis = f"Lower RSI entry threshold by 2 (from {strategy['entry']['threshold']})"
        return candidate, hypothesis, True

    if strategy["stop_loss_pct"] > 1.0:
        candidate["stop_loss_pct"] -= 0.2
        hypothesis = f"Tighten stop_loss_pct by 0.2 (from {strategy['stop_loss_pct']})"
        return candidate, hypothesis, True

    return candidate, "No change needed this cycle", False

def log_hypothesis(record):
    with open(HYPOTHESES_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

def fallback_reflect(goal, days=30):
    strategy = load_strategy()
    old_version = strategy["version"]
    candidate, hypothesis, changed = propose_change(strategy)

    if not changed:
        print(hypothesis)
        log_hypothesis({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version_from": old_version,
            "version_to": old_version,
            "hypothesis": hypothesis,
            "mode": "fallback",
            "accepted": False,
        })
        return

    asset = goal["asset"]
    try:
        closes, timestamps = fetch_history(asset, days)
    except Exception as e:
        closes, timestamps = [], []
        print(f"Could not fetch historical data to validate reflection: {e}")

    if len(closes) <= RSI_WINDOW:
        print(f"Not enough history for {asset} over {days}d to validate this "
              f"reflection — leaving v{old_version} unchanged.")
        log_hypothesis({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version_from": old_version,
            "version_to": old_version,
            "hypothesis": hypothesis,
            "mode": "fallback",
            "accepted": False,
            "reason": "insufficient_backtest_data",
        })
        return

    baseline = summarize(simulate(closes, timestamps, strategy), goal)

    new_version = str(int(old_version) + 1).zfill(2)
    candidate["version"] = new_version
    candidate_result = summarize(simulate(closes, timestamps, candidate), goal)

    accepted = candidate_result["score"] >= baseline["score"]

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version_from": old_version,
        "version_to": new_version if accepted else old_version,
        "hypothesis": hypothesis,
        "mode": "fallback",
        "accepted": accepted,
        "baseline_score": baseline["score"],
        "candidate_score": candidate_result["score"],
    }
    log_hypothesis(record)

    if accepted:
        HISTORY_DIR.mkdir(exist_ok=True)
        shutil.copy(STRATEGY_FILE, HISTORY_DIR / f"v{old_version}.yaml")
        save_strategy(candidate)
        print(f"Reflection complete: v{old_version} → v{new_version} "
              f"(backtest score {baseline['score']} → {candidate_result['score']})")
    else:
        print(f"Reflection rejected: v{old_version} unchanged "
              f"(backtest score would go {baseline['score']} → {candidate_result['score']})")
    print(hypothesis)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--hermes", action="store_true")
    parser.add_argument("--days", type=int, default=30,
                         help="days of history to backtest a proposed change against")
    args = parser.parse_args()

    goal_path = STATE_DIR / "goal.yaml"
    with open(goal_path) as f:
        goal = yaml.safe_load(f)

    if args.fallback:
        fallback_reflect(goal, days=args.days)
    else:
        print("Hermes mode not yet active — use --fallback for now")

if __name__ == "__main__":
    main()

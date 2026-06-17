#!/usr/bin/env python3
"""Reflection engine — fallback (deterministic) and hermes modes."""
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
import yaml

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

def bump_version(current: str) -> str:
    return f"{int(current):02d}"

def fallback_reflect(goal):
    strategy = load_strategy()
    old_version = strategy["version"]
    new_version = str(int(old_version) + 1).zfill(2)

    # Save prior version
    HISTORY_DIR.mkdir(exist_ok=True)
    shutil.copy(STRATEGY_FILE, HISTORY_DIR / f"v{old_version}.yaml")

    changed = False
    # Rule: if we want to loosen entry when underperforming
    if strategy["entry"]["threshold"] > 25:
        strategy["entry"]["threshold"] -= 2
        hypothesis = f"Lowered RSI entry threshold by 2 (from {old_version})"
        changed = True
    elif strategy["stop_loss_pct"] > 1.0:
        strategy["stop_loss_pct"] -= 0.2
        hypothesis = f"Tightened stop_loss_pct by 0.2 (from {old_version})"
        changed = True

    if not changed:
        hypothesis = "No change needed this cycle"

    strategy["version"] = new_version
    save_strategy(strategy)

    with open(HYPOTHESES_FILE, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.utcnow().isoformat(),
            "version_from": old_version,
            "version_to": new_version,
            "hypothesis": hypothesis,
            "mode": "fallback"
        }) + "\n")

    print(f"Reflection complete: v{old_version} → v{new_version}")
    print(hypothesis)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--hermes", action="store_true")
    args = parser.parse_args()

    goal_path = STATE_DIR / "goal.yaml"
    with open(goal_path) as f:
        goal = yaml.safe_load(f)

    if args.fallback:
        fallback_reflect(goal)
    else:
        print("Hermes mode not yet active — use --fallback for now")

if __name__ == "__main__":
    main()

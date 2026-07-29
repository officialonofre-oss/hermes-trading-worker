#!/usr/bin/env python3
"""Entry point for the Hermes trading worker."""
import argparse
import asyncio
import yaml
from pathlib import Path
from hermes_trading.loop import trading_loop

def load_goal():
    goal_path = Path(__file__).parent.parent / "state" / "goal.yaml"
    if goal_path.exists():
        with open(goal_path) as f:
            return yaml.safe_load(f)
    # Default, mirrors state/goal.yaml -- only used if state/ is somehow
    # missing entirely; normally the Docker entrypoint seeds it before this runs.
    return {
        "asset": "BTC/USDT",
        "target_return_30d": 0.05,
        "max_drawdown": 0.08,
        "min_sharpe": 1.2,
        "failure_below": -0.04,
        "reflection_every": 5,
        "one_variable_only": True,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default=None)
    args = parser.parse_args()

    goal = load_goal()
    asset = args.asset or goal["asset"]

    print(f"Booting hermes-trading worker for {asset} (paper mode)")
    asyncio.run(trading_loop(asset, goal))

if __name__ == "__main__":
    main()

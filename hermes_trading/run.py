#!/usr/bin/env python3
"""Entry point for the Hermes trading worker."""
import argparse
import asyncio
import yaml
from pathlib import Path
from hermes_trading.loop import trading_loop

def load_goal():
    goal_path = Path(__file__).parent.parent / "state" / "goal.yaml"
    with open(goal_path) as f:
        return yaml.safe_load(f)

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

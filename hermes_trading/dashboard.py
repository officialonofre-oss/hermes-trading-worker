#!/usr/bin/env python3
"""Generates a static HTML dashboard from state/*.jsonl -- a decision log
showing what the bot traded, why (the RSI/sentiment/onchain signals that
triggered each entry), and what reflection has done to the strategy over
time. Reads state/ only; never mutates it."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import yaml

from hermes_trading.score import score_trades, max_drawdown, sharpe_ratio

STATE_DIR = Path(__file__).parent.parent / "state"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"
GOAL_FILE = STATE_DIR / "goal.yaml"
TRADES_FILE = STATE_DIR / "trades.jsonl"
HYPOTHESES_FILE = STATE_DIR / "hypotheses.jsonl"
BACKTESTS_FILE = STATE_DIR / "backtests.jsonl"
OUTPUT_FILE = STATE_DIR / "dashboard.html"

TEMPLATE_FILE = Path(__file__).parent / "dashboard_template.html"


def _read_yaml(path: Path) -> Dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _read_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # heartbeat.json is rewritten in place every cycle, so a read landing
        # mid-write can see a truncated file. Not worth failing the page over.
        return {}


def build_data(state_dir: Path = STATE_DIR) -> Dict:
    strategy = _read_yaml(state_dir / "strategy.yaml")
    goal = _read_yaml(state_dir / "goal.yaml")
    trades = _read_jsonl(state_dir / "trades.jsonl")
    hypotheses = _read_jsonl(state_dir / "hypotheses.jsonl")
    backtests = _read_jsonl(state_dir / "backtests.jsonl")
    heartbeat = _read_json(state_dir / "heartbeat.json")

    closed = [t for t in trades if t.get("closed")]
    wins = [t for t in closed if t.get("pnl", 0) > 0]

    summary = {
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "total_return": round(sum(t.get("pnl", 0) for t in closed), 4),
        "score": round(score_trades(trades, goal), 4) if trades else None,
        "max_drawdown": round(max_drawdown([t["pnl"] for t in closed]), 4) if closed else None,
        "sharpe": round(sharpe_ratio([t["pnl"] for t in closed]), 4) if closed else None,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": len(trades) == 0,
        "strategy": strategy,
        "goal": goal,
        "heartbeat": heartbeat,
        "summary": summary,
        "trades": list(reversed(trades))[:100],
        "hypotheses": list(reversed(hypotheses))[:50],
        "backtests": list(reversed(backtests))[:50],
    }


def render(data: Dict) -> str:
    template = TEMPLATE_FILE.read_text()
    # Escape "<" before embedding the JSON in a <script> block. Every field
    # today is a fixed enum or admin-set config, so nothing can currently
    # carry markup -- but the moment someone adds a free-text field (a real
    # news headline, an API error string), an unescaped "</script>" would
    # break out of the block and execute. < is valid inside a JSON
    # string and parses back to "<", so the data itself is unchanged.
    payload = json.dumps(data).replace("<", "\\u003c")
    return template.replace("__DASHBOARD_DATA__", payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUTPUT_FILE))
    args = parser.parse_args()

    data = build_data()
    html = render(data)
    out_path = Path(args.out)
    out_path.write_text(html)
    print(f"Dashboard written to {out_path}")
    if data["is_sample"]:
        print("Note: state/trades.jsonl is empty -- the dashboard will show "
              "sample data until the live loop has logged real trades.")


if __name__ == "__main__":
    main()

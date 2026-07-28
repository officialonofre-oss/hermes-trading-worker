#!/usr/bin/env python3
"""Backtest the current (or a given) strategy against historical price data,
using the exact same entry/exit rules the live loop uses."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import yaml
import yfinance as yf

from hermes_trading.adapters.price import _to_yfinance_ticker, compute_rsi
from hermes_trading.strategy_rules import evaluate_entry, evaluate_exit
from hermes_trading.score import score_trades

STATE_DIR = Path(__file__).parent.parent / "state"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"
GOAL_FILE = STATE_DIR / "goal.yaml"
BACKTESTS_FILE = STATE_DIR / "backtests.jsonl"

RSI_WINDOW = 72  # hourly candles, matches fetch_price's period="3d" interval="1h"


def load_yaml(path: Path) -> Dict:
    with open(path) as f:
        return yaml.safe_load(f)


def fetch_history(symbol: str, days: int) -> Tuple[List[float], List]:
    ticker = yf.Ticker(_to_yfinance_ticker(symbol))
    hist = ticker.history(period=f"{days}d", interval="1h")
    return hist["Close"].tolist(), list(hist.index)


def simulate(closes: List[float], timestamps: List, strategy: Dict) -> List[Dict]:
    """Walk the historical bars in order, evaluating the same entry/exit
    rules the live loop uses. Only one position is held open at a time —
    the live loop has no such guard yet, so this is a simplifying
    assumption for the backtest, not a guarantee of live parity."""
    trades = []
    open_trade = None

    for i in range(RSI_WINDOW, len(closes)):
        rsi = compute_rsi(closes[i - RSI_WINDOW:i])
        price = closes[i]
        ts = timestamps[i]

        if open_trade:
            exit_info = evaluate_exit(open_trade["entry_price"], price, strategy)
            if exit_info:
                open_trade["closed"] = True
                open_trade["exit_price"] = price
                open_trade["exit_reason"] = exit_info["exit_reason"]
                open_trade["pnl"] = exit_info["pnl"]
                open_trade["closed_at"] = ts.isoformat()
                trades.append(open_trade)
                open_trade = None

        if not open_trade:
            decision = evaluate_entry(rsi, strategy)
            if decision:
                open_trade = {
                    "timestamp": ts.isoformat(),
                    "decision": decision,
                    "entry_price": price,
                    "strategy_version": strategy["version"],
                    "pnl": 0.0,
                    "closed": False,
                }

    if open_trade:
        trades.append(open_trade)  # still open at the end of the window

    return trades


def summarize(trades: List[Dict], goal: Dict) -> Dict:
    closed = [t for t in trades if t.get("closed")]
    wins = [t for t in closed if t["pnl"] > 0]
    return {
        "num_trades": len(trades),
        "closed_trades": len(closed),
        "still_open": len(trades) - len(closed),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "total_return": round(sum(t["pnl"] for t in closed), 4),
        "score": round(score_trades(trades, goal), 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default=None)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    goal = load_yaml(GOAL_FILE)
    strategy = load_yaml(STRATEGY_FILE)
    asset = args.asset or goal["asset"]

    closes, timestamps = fetch_history(asset, args.days)
    if len(closes) <= RSI_WINDOW:
        print(f"Not enough history for {asset} over {args.days}d to backtest "
              f"(got {len(closes)} hourly candles, need > {RSI_WINDOW}).")
        return

    trades = simulate(closes, timestamps, strategy)
    result = summarize(trades, goal)

    print(f"Backtest: {asset} over {args.days}d, strategy v{strategy['version']}")
    for key, value in result.items():
        print(f"  {key}: {value}")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "asset": asset,
        "days": args.days,
        "strategy_version": strategy["version"],
        **result,
    }
    with open(BACKTESTS_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()

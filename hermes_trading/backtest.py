#!/usr/bin/env python3
"""Backtest the current (or a given) strategy against historical price data,
using the exact same entry/exit rules the live loop uses."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import httpx
import yaml
import yfinance as yf

from hermes_trading.adapters.price import _to_yfinance_ticker, compute_rsi
from hermes_trading.adapters.news import FEAR_GREED_URL, classify_sentiment
from hermes_trading.adapters.onchain import (
    COINMETRICS_URL, METRIC as ONCHAIN_METRIC, TREND_LOOKBACK_DAYS,
    _to_coinmetrics_asset, _sorted_values, classify_trend,
)
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


def fetch_sentiment_history(days: int) -> Dict[str, str]:
    """Date -> sentiment ("bearish"/"neutral"/"bullish"), via the same Fear
    & Greed Index news.py uses live. Returns {} (never blocks entries) if
    the call fails, matching the live adapter's fallback behavior."""
    try:
        resp = httpx.get(FEAR_GREED_URL, params={"limit": days + 2, "format": "json"}, timeout=10)
        resp.raise_for_status()
        result = {}
        for row in resp.json().get("data", []):
            day = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc).date().isoformat()
            result[day] = classify_sentiment(int(row["value"]))
        return result
    except Exception:
        return {}


def fetch_onchain_trend_history(symbol: str, days: int) -> Dict[str, str]:
    """Date -> onchain trend ("declining"/"stable"/"growing"/"unknown"), via
    the same Coin Metrics active-addresses series onchain.py uses live.
    Fetches extra lookback so the first requested day still has ~7 prior
    days to compare against. Returns {} (never blocks entries) on failure."""
    try:
        start_time = (datetime.now(timezone.utc) - timedelta(days=days + TREND_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = httpx.get(COINMETRICS_URL, params={
            "assets": _to_coinmetrics_asset(symbol),
            "metrics": ONCHAIN_METRIC,
            "frequency": "1d",
            "start_time": start_time,
            "page_size": days + TREND_LOOKBACK_DAYS + 5,
        }, timeout=10)
        resp.raise_for_status()
        rows = _sorted_values(resp.json().get("data", []))
        trend_by_date = {}
        for i in range(len(rows)):
            day = rows[i][0][:10]  # "YYYY-MM-DD" prefix of the ISO timestamp
            trend_by_date[day] = classify_trend([v for _, v in rows[:i + 1]])
        return trend_by_date
    except Exception:
        return {}


def simulate(closes: List[float], timestamps: List, strategy: Dict,
             sentiment_by_date: Dict[str, str] = None,
             onchain_trend_by_date: Dict[str, str] = None) -> List[Dict]:
    """Walk the historical bars in order, evaluating the same entry/exit
    rules the live loop uses. Only one position is held open at a time —
    the live loop has no such guard yet, so this is a simplifying
    assumption for the backtest, not a guarantee of live parity.
    sentiment_by_date/onchain_trend_by_date are optional -- when omitted,
    those signals default to "neutral"/"unknown" for every bar, same as
    evaluate_entry's veto-only defaults."""
    sentiment_by_date = sentiment_by_date or {}
    onchain_trend_by_date = onchain_trend_by_date or {}
    trades = []
    open_trade = None

    for i in range(RSI_WINDOW, len(closes)):
        rsi = compute_rsi(closes[i - RSI_WINDOW:i])
        price = closes[i]
        ts = timestamps[i]
        day = ts.date().isoformat()

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
            sentiment = sentiment_by_date.get(day, "neutral")
            onchain_trend = onchain_trend_by_date.get(day, "unknown")
            decision = evaluate_entry(rsi, strategy, sentiment=sentiment, onchain_trend=onchain_trend)
            if decision:
                open_trade = {
                    "timestamp": ts.isoformat(),
                    "decision": decision,
                    "entry_price": price,
                    "strategy_version": strategy["version"],
                    "signals": {"rsi": round(rsi, 2), "sentiment": sentiment, "onchain_trend": onchain_trend},
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

    sentiment_by_date = fetch_sentiment_history(args.days)
    onchain_trend_by_date = fetch_onchain_trend_history(asset, args.days)

    trades = simulate(closes, timestamps, strategy, sentiment_by_date, onchain_trend_by_date)
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

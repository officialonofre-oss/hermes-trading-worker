import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import yaml
from hermes_trading.score import score_trades
from hermes_trading.adapters.price import fetch_price
from hermes_trading.adapters.onchain import fetch_onchain
from hermes_trading.adapters.news import fetch_news
from hermes_trading.adapters.macro import fetch_macro

STATE_DIR = Path(__file__).parent.parent / "state"
TRADES_FILE = STATE_DIR / "trades.jsonl"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
STRATEGY_FILE = STATE_DIR / "strategy.yaml"

async def load_strategy():
    if STRATEGY_FILE.exists():
        with open(STRATEGY_FILE) as f:
            return yaml.safe_load(f)
    # default v01
    return {
        "version": "01",
        "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
        "stop_loss_pct": 2.0,
        "take_profit_pct": 4.0,
        "position_size_r": 0.5
    }

async def paper_trade(decision, price_data, strategy):
    # Very simple paper trade simulator
    trade = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "asset": price_data.get("symbol"),
        "decision": decision,
        "entry_price": price_data.get("price"),
        "strategy_version": strategy["version"],
        "pnl": 0.0,
        "closed": False
    }
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(trade) + "\n")
    return trade

async def close_open_trades(price_data, strategy):
    """Check open trades for this asset against the current price and close
    any that have hit their stop-loss or take-profit level."""
    if not TRADES_FILE.exists():
        return

    with open(TRADES_FILE) as f:
        trades = [json.loads(line) for line in f if line.strip()]

    current_price = price_data.get("price")
    symbol = price_data.get("symbol")
    stop_loss_pct = strategy.get("stop_loss_pct", 2.0)
    take_profit_pct = strategy.get("take_profit_pct", stop_loss_pct * 2)
    changed = False

    for trade in trades:
        if trade.get("closed") or trade.get("asset") != symbol:
            continue
        if trade.get("decision") != "enter_long":
            continue

        entry_price = trade["entry_price"]
        change_pct = (current_price - entry_price) / entry_price * 100

        if change_pct <= -stop_loss_pct:
            exit_reason = "stop_loss"
        elif change_pct >= take_profit_pct:
            exit_reason = "take_profit"
        else:
            continue

        trade["closed"] = True
        trade["exit_price"] = current_price
        trade["exit_reason"] = exit_reason
        trade["pnl"] = change_pct / 100
        trade["closed_at"] = datetime.now(timezone.utc).isoformat()
        changed = True

    if changed:
        with open(TRADES_FILE, "w") as f:
            for trade in trades:
                f.write(json.dumps(trade) + "\n")

async def trading_loop(asset, goal):
    consecutive_failures = 0
    last_reflection_count = 0

    while True:
        try:
            price = await fetch_price(None, asset)
            onchain = await fetch_onchain(asset)
            news = await fetch_news(asset)
            macro = await fetch_macro()

            strategy = await load_strategy()

            await close_open_trades(price, strategy)

            # Extremely simple RSI-like decision for starter
            rsi = price.get("rsi", 50)
            decision = None
            if strategy["entry"]["direction"] == "long" and rsi < strategy["entry"]["threshold"]:
                decision = "enter_long"

            if decision:
                await paper_trade(decision, price, strategy)

            # heartbeat
            heartbeat = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "asset": asset,
                "last_price": price.get("price"),
                "consecutive_failures": consecutive_failures
            }
            with open(HEARTBEAT_FILE, "w") as f:
                json.dump(heartbeat, f)

            consecutive_failures = 0
            await asyncio.sleep(60)  # 1 minute cycle

        except Exception as e:
            consecutive_failures += 1
            print(f"Loop error: {e}")
            if consecutive_failures >= 5:
                print("Circuit breaker tripped")
                break
            await asyncio.sleep(30)
# Rebuild trigger: Wed, Jun 17, 2026  6:04:47 PM

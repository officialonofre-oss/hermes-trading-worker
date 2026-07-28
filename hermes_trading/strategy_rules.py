"""Pure entry/exit decision rules, shared by the live loop and the backtester
so a strategy is always evaluated identically in both places."""
from typing import Dict, Optional


def evaluate_entry(rsi: float, strategy: Dict) -> Optional[str]:
    entry = strategy["entry"]
    if entry["direction"] == "long" and rsi < entry["threshold"]:
        return "enter_long"
    return None


def evaluate_exit(entry_price: float, current_price: float, strategy: Dict) -> Optional[Dict]:
    stop_loss_pct = strategy.get("stop_loss_pct", 2.0)
    take_profit_pct = strategy.get("take_profit_pct", stop_loss_pct * 2)
    change_pct = (current_price - entry_price) / entry_price * 100

    if change_pct <= -stop_loss_pct:
        return {"exit_reason": "stop_loss", "pnl": change_pct / 100}
    if change_pct >= take_profit_pct:
        return {"exit_reason": "take_profit", "pnl": change_pct / 100}
    return None

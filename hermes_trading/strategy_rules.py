"""Pure entry/exit decision rules, shared by the live loop and the backtester
so a strategy is always evaluated identically in both places."""
from typing import Dict, List, Optional


def evaluate_entry(rsi: float, strategy: Dict, sentiment: str = "neutral",
                    onchain_trend: str = "unknown") -> Optional[str]:
    """RSI is the primary signal; sentiment/onchain act as a veto, not a
    confirmation requirement -- a bearish sentiment or declining onchain
    trend blocks entry, but "neutral"/"unknown" (including missing or
    unavailable data) never does. That keeps a brief secondary data-source
    outage from silently halting trading altogether."""
    entry = strategy["entry"]
    if entry["direction"] != "long" or rsi >= entry["threshold"]:
        return None
    if sentiment == "bearish":
        return None
    if onchain_trend == "declining":
        return None
    return "enter_long"


def explain_entry(rsi: float, strategy: Dict, sentiment: str = "neutral",
                  onchain_trend: str = "unknown") -> List[Dict]:
    """The same gates evaluate_entry applies, but reported individually
    instead of short-circuiting -- so the dashboard can show *why* no trade
    fired ("RSI 45.2, needs < 28") rather than just showing nothing.

    These two functions must agree: there's a property test asserting
    evaluate_entry returns a decision exactly when no gate here is blocking.
    Change one, change the other."""
    entry = strategy["entry"]
    threshold = entry["threshold"]
    return [
        {
            "name": "direction",
            "passing": entry["direction"] == "long",
            "detail": f"strategy direction is {entry['direction']}",
        },
        {
            "name": "rsi",
            "passing": rsi < threshold,
            "detail": f"RSI {round(rsi, 2)} vs threshold {threshold}",
        },
        {
            "name": "sentiment",
            "passing": sentiment != "bearish",
            "detail": f"sentiment is {sentiment}",
        },
        {
            "name": "onchain_trend",
            "passing": onchain_trend != "declining",
            "detail": f"onchain trend is {onchain_trend}",
        },
    ]


def evaluate_exit(entry_price: float, current_price: float, strategy: Dict) -> Optional[Dict]:
    stop_loss_pct = strategy.get("stop_loss_pct", 2.0)
    take_profit_pct = strategy.get("take_profit_pct", stop_loss_pct * 2)
    change_pct = (current_price - entry_price) / entry_price * 100

    if change_pct <= -stop_loss_pct:
        return {"exit_reason": "stop_loss", "pnl": change_pct / 100}
    if change_pct >= take_profit_pct:
        return {"exit_reason": "take_profit", "pnl": change_pct / 100}
    return None

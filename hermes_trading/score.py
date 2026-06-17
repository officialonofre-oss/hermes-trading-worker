from typing import List, Dict
import numpy as np

def score_trades(trades: List[Dict], goal: Dict) -> float:
    if not trades:
        return 0.0

    returns = [t.get("pnl", 0.0) for t in trades if t.get("closed")]
    if not returns:
        return 0.0

    realised_return = sum(returns)
    target = goal["target_return_30d"]
    max_dd = goal["max_drawdown"]
    min_sharpe = goal["min_sharpe"]

    # Simple composite score in [-1, 1]
    ret_score = min(max(realised_return / target, -1), 1) if target > 0 else 0
    dd_score = -min(max(max_dd, 0), 1)  # penalise drawdown
    sharpe_score = min(max((min_sharpe - 0.5) / 1.5, 0), 1)  # placeholder

    score = 0.5 * ret_score + 0.3 * dd_score + 0.2 * sharpe_score
    return float(np.clip(score, -1.0, 1.0))

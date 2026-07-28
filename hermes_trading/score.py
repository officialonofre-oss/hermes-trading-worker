from typing import List, Dict
import numpy as np

def max_drawdown(returns: List[float]) -> float:
    """Largest peak-to-trough drop in cumulative equity, as a fraction
    (0.05 == a 5% drawdown), walking the trades in order."""
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst

def sharpe_ratio(returns: List[float]) -> float:
    """Mean/stdev of per-trade returns. Not annualised: trades are
    event-driven rather than fixed-period, so there's no natural
    trades-per-year figure to scale by."""
    if len(returns) < 2:
        return 0.0
    std = float(np.std(returns, ddof=1))
    if std == 0:
        return 0.0
    return float(np.mean(returns)) / std

def score_trades(trades: List[Dict], goal: Dict) -> float:
    if not trades:
        return 0.0

    returns = [t.get("pnl", 0.0) for t in trades if t.get("closed")]
    if not returns:
        return 0.0

    realised_return = sum(returns)
    realised_dd = max_drawdown(returns)
    realised_sharpe = sharpe_ratio(returns)

    target = goal["target_return_30d"]
    max_dd_limit = goal["max_drawdown"]
    min_sharpe = goal["min_sharpe"]

    # Simple composite score in [-1, 1]
    ret_score = min(max(realised_return / target, -1), 1) if target > 0 else 0
    dd_score = -min(max(realised_dd / max_dd_limit, 0), 1) if max_dd_limit > 0 else 0
    sharpe_score = min(max(realised_sharpe / min_sharpe, 0), 1) if min_sharpe > 0 else 0

    score = 0.5 * ret_score + 0.3 * dd_score + 0.2 * sharpe_score
    return float(np.clip(score, -1.0, 1.0))

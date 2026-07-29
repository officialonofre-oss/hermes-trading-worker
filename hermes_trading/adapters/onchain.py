from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
import httpx

COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
METRIC = "AdrActCnt"  # active address count -- free on Coin Metrics' Community API, no key needed
TREND_LOOKBACK_DAYS = 12  # >= 8 daily points needed to compare current vs ~7 days ago


def _to_coinmetrics_asset(symbol: str) -> str:
    return symbol.split("/")[0].lower()


def _sorted_values(data: List[Dict]) -> List[Tuple[str, float]]:
    rows = [(row["time"], float(row[METRIC])) for row in data if row.get(METRIC) not in (None, "")]
    return sorted(rows, key=lambda row: row[0])


def classify_trend(values: List[float]) -> str:
    """Compares the most recent value to the one 7 entries back. At daily
    frequency that approximates 7 days earlier -- the same positional-window
    style price.py's RSI calc already uses, no date-matching needed."""
    if len(values) < 8:
        return "unknown"
    current, past = values[-1], values[-8]
    if past == 0:
        return "unknown"
    change_pct = (current - past) / past * 100
    if change_pct <= -5:
        return "declining"
    if change_pct >= 5:
        return "growing"
    return "stable"


async def fetch_onchain(symbol: str) -> Dict:
    try:
        start_time = (datetime.now(timezone.utc) - timedelta(days=TREND_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(COINMETRICS_URL, params={
                "assets": _to_coinmetrics_asset(symbol),
                "metrics": METRIC,
                "frequency": "1d",
                "start_time": start_time,
                "page_size": TREND_LOOKBACK_DAYS + 2,
            })
            resp.raise_for_status()
            rows = _sorted_values(resp.json().get("data", []))
            if rows:
                as_of, active_addresses = rows[-1][0], int(rows[-1][1])
                trend = classify_trend([v for _, v in rows])
                return {
                    "schema_version": "1.0",
                    "symbol": symbol,
                    "active_addresses": active_addresses,
                    "trend": trend,
                    "as_of": as_of,
                    "source": "coinmetrics",
                }
    except Exception:
        pass

    # Fallback: the API call failed, or returned no usable data
    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "active_addresses": 0,
        "trend": "unknown",
        "note": "using public fallback",
    }

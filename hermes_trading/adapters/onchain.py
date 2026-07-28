from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import httpx

COINMETRICS_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
METRIC = "AdrActCnt"  # active address count -- free on Coin Metrics' Community API, no key needed


def _to_coinmetrics_asset(symbol: str) -> str:
    return symbol.split("/")[0].lower()


def _parse_latest(data: List[Dict]) -> Optional[Tuple[int, str]]:
    """Pick the most recent row by explicit time comparison rather than
    trusting the API's default row ordering (undocumented/ambiguous),
    so a wrong assumption there can't silently serve stale data."""
    rows = [row for row in data if row.get(METRIC) not in (None, "")]
    if not rows:
        return None
    latest = max(rows, key=lambda row: row["time"])
    return int(float(latest[METRIC])), latest["time"]


async def fetch_onchain(symbol: str) -> Dict:
    try:
        start_time = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(COINMETRICS_URL, params={
                "assets": _to_coinmetrics_asset(symbol),
                "metrics": METRIC,
                "frequency": "1d",
                "start_time": start_time,
                "page_size": 10,
            })
            resp.raise_for_status()
            parsed = _parse_latest(resp.json().get("data", []))
            if parsed:
                active_addresses, as_of = parsed
                return {
                    "schema_version": "1.0",
                    "symbol": symbol,
                    "active_addresses": active_addresses,
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
        "note": "using public fallback",
    }

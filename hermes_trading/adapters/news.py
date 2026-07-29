from typing import Dict, List, Optional, Tuple
import httpx

FEAR_GREED_URL = "https://api.alternative.me/fng/"


def classify_sentiment(value: int) -> str:
    if value <= 40:
        return "bearish"
    if value >= 60:
        return "bullish"
    return "neutral"


def _parse_latest(data: List[Dict]) -> Optional[Tuple[int, str, str]]:
    if not data:
        return None
    latest = data[0]
    return int(latest["value"]), latest.get("value_classification", ""), latest.get("timestamp", "")


async def fetch_news(symbol: str) -> Dict:
    """Market-wide crypto sentiment via the Fear & Greed Index -- free, no
    key required. Note this is a single index across the whole crypto
    market, not per-asset, so the same value is returned regardless of
    which symbol is requested."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(FEAR_GREED_URL, params={"limit": 1, "format": "json"})
            resp.raise_for_status()
            parsed = _parse_latest(resp.json().get("data", []))
            if parsed:
                value, label, as_of = parsed
                return {
                    "schema_version": "1.0",
                    "symbol": symbol,
                    "sentiment": classify_sentiment(value),
                    "fear_greed_value": value,
                    "fear_greed_label": label,
                    "as_of": as_of,
                    "source": "alternative.me",
                }
    except Exception:
        pass

    # Fallback: the API call failed, or returned no usable data
    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "sentiment": "neutral",
    }

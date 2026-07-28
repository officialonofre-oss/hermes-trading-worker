import os
from typing import Dict, List, Optional, Tuple
import httpx
from dotenv import load_dotenv

load_dotenv()

GLASSNODE_URL = "https://api.glassnode.com/v1/metrics/addresses/active_count"


def _to_glassnode_asset(symbol: str) -> str:
    return symbol.split("/")[0]


def _parse_latest(data: List[Dict]) -> Optional[Tuple[int, int]]:
    if not data:
        return None
    latest = data[-1]
    return int(latest["v"]), latest["t"]


async def fetch_onchain(symbol: str) -> Dict:
    api_key = os.getenv("GLASSNODE_API_KEY")
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(GLASSNODE_URL, params={
                    "a": _to_glassnode_asset(symbol),
                    "api_key": api_key,
                    "i": "24h",
                })
                resp.raise_for_status()
                parsed = _parse_latest(resp.json())
                if parsed:
                    active_addresses, as_of = parsed
                    return {
                        "schema_version": "1.0",
                        "symbol": symbol,
                        "active_addresses": active_addresses,
                        "as_of": as_of,
                        "source": "glassnode",
                    }
        except Exception:
            pass

    # Fallback: no key configured, or the API call failed
    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "active_addresses": 0,
        "note": "using public fallback",
    }

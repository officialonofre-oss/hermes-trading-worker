from typing import Dict
import httpx

async def fetch_onchain(symbol: str) -> Dict:
    # Free public endpoint fallback (glassnode-style placeholder)
    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "active_addresses": 0,
        "note": "using public fallback"
    }

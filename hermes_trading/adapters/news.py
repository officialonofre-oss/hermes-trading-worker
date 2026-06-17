from typing import Dict
import httpx

async def fetch_news(symbol: str) -> Dict:
    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "sentiment": "neutral",
        "headlines": []
    }

import os
from typing import Dict, List
import httpx
from dotenv import load_dotenv

load_dotenv()

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


def _to_cryptopanic_currency(symbol: str) -> str:
    return symbol.split("/")[0]


def _derive_sentiment(posts: List[Dict]) -> str:
    positive = sum(p.get("votes", {}).get("positive", 0) for p in posts)
    negative = sum(p.get("votes", {}).get("negative", 0) for p in posts)
    if positive > negative * 1.2:
        return "bullish"
    if negative > positive * 1.2:
        return "bearish"
    return "neutral"


async def fetch_news(symbol: str) -> Dict:
    auth_token = os.getenv("CRYPTOPANIC_API_KEY")
    if auth_token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(CRYPTOPANIC_URL, params={
                    "auth_token": auth_token,
                    "currencies": _to_cryptopanic_currency(symbol),
                    "kind": "news",
                    "public": "true",
                })
                resp.raise_for_status()
                posts = resp.json().get("results", [])
                return {
                    "schema_version": "1.0",
                    "symbol": symbol,
                    "sentiment": _derive_sentiment(posts),
                    "headlines": [p.get("title") for p in posts[:5]],
                    "source": "cryptopanic",
                }
        except Exception:
            pass

    # Fallback: no key configured, or the API call failed
    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "sentiment": "neutral",
        "headlines": [],
    }

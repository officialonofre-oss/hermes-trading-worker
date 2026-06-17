from typing import Dict

async def fetch_macro() -> Dict:
    return {
        "schema_version": "1.0",
        "dxy": 104.5,
        "fed_funds": 5.25
    }

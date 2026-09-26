import asyncio
import httpx
import uuid
from app.config import settings
from app.services.apify_service import parse_apify_record

token = settings.APIFY_API_TOKEN
datasets = {
    "tiktok": "A5vYTVGGEuUz4EQIn",
    "instagram": "BRPeqTOiCJNzXFLKD",
    "twitter": "Trn0p5CIkIrBA2PF8",
    "reddit": "AdZEs7GNi1iIYvYfR",
    "youtube": "RmvkTop1KSy1Avj1Z"
}

async def test_parse():
    kid = uuid.uuid4()
    async with httpx.AsyncClient(timeout=15) as client:
        for p, ds_id in datasets.items():
            r = await client.get(f"https://api.apify.com/v2/datasets/{ds_id}/items?token={token}")
            if r.status_code == 200:
                items = r.json()
                print(f"\n[{p}] Raw items fetched: {len(items)}")
                if items:
                    print(f"Sample raw keys for {p}: {list(items[0].keys())[:10]}")
                    try:
                        record = parse_apify_record(items[0], kid)
                        print(f"Parsed {p}: platform={record.platform}, url={record.post_url}")
                        print(f"Text: {repr(record.comment_text[:80])}")
                    except Exception as e:
                        print(f"FAILED to parse {p}: {e}")
            else:
                print(f"Failed to fetch {p}: HTTP {r.status_code}")

asyncio.run(test_parse())

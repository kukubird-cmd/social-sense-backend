import asyncio
import httpx
from app.config import settings

token = settings.APIFY_API_TOKEN
run_ids = {
    "tiktok": "0dZ2caGD6iFtp9DHF",
    "instagram": "6Ums7re7ByxtQVFzC",
    "twitter": "b27yMhCEVbpK8gC5N",
    "reddit": "bGIkG0PutTizfLrHv",
    "youtube": "2dlJqaPLg44DsY1Q7"
}

async def check():
    async with httpx.AsyncClient(timeout=10) as client:
        for p, rid in run_ids.items():
            r = await client.get(f"https://api.apify.com/v2/actor-runs/{rid}?token={token}")
            if r.status_code == 200:
                data = r.json().get("data", {})
                st = data.get("status")
                ds_id = data.get("defaultDatasetId")
                # check dataset items count
                item_count = 0
                if ds_id:
                    ds_r = await client.get(f"https://api.apify.com/v2/datasets/{ds_id}?token={token}")
                    if ds_r.status_code == 200:
                        item_count = ds_r.json().get("data", {}).get("itemCount", 0)
                print(f"[{p}] status={st} | dataset={ds_id} | itemCount={item_count}")
            else:
                print(f"[{p}] HTTP {r.status_code}")

asyncio.run(check())

import httpx
import asyncio

async def main():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://social-sense-backend-production.up.railway.app/health")
            print("Status:", r.status_code)
            print("Body:", r.text[:200])
    except Exception as e:
        print("Error:", e)

asyncio.run(main())

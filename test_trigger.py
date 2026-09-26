import asyncio
import uuid
from app.routers.scraper import _trigger_actor, APIFY_ACTORS, _build_actor_input

async def main():
    kw = "orchan consulting asia"
    kid = uuid.uuid4()
    for p in APIFY_ACTORS:
        inp = _build_actor_input(p, kw)
        print(f"Platform: {p} | Input: {inp}")
        try:
            run = await _trigger_actor(p, kw, kid)
            run_id = run.get("id")
            status = run.get("status")
            print(f" -> SUCCESS: runId={run_id}, status={status}")
        except Exception as e:
            print(f" -> FAILED for {p}: {e}")

asyncio.run(main())

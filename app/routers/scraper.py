"""
Scraper Router — Triggers real Apify Actors for live social media scraping.
Supports auto-detected public tunnels (localtunnel / ngrok / cloudflare)
AND includes bulletproof background auto-polling so real data is ingested
and broadcast live via WebSockets even without any public tunnel configured!
"""
import os
import uuid
import logging
import asyncio
import httpx
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models import Keyword
from app.schemas import KeywordCreate
from app.services.billing_service import create_keyword_for_company
from app.services.tunnel_manager import (
    get_tunnel_status as tm_get_tunnel_status,
    start_background_tunnel,
    set_current_url,
    get_current_url,
)
from app.routers.webhooks import process_apify_dataset_task

logger = logging.getLogger("scraper")
router = APIRouter(prefix="/api/scraper", tags=["Live Scraper"])

# ---------------------------------------------------------------------------
# Apify Actor IDs — Battle-tested public actors for all 5 platforms
# ---------------------------------------------------------------------------
APIFY_ACTORS = {
    "tiktok":    "clockworks/free-tiktok-scraper",
    "instagram": "apify/instagram-scraper",
    "twitter":   "apidojo/tweet-scraper",
    "reddit":    "trudax/reddit-scraper-lite",
    "youtube":   "blackfalcondata/youtube-comment-scraper",
}

APIFY_BASE = "https://api.apify.com/v2"


class TunnelUpdateRequest(BaseModel):
    tunnel_url: str


class TunnelStartRequest(BaseModel):
    port: int = 8000
    preferred: str = "ngrok"


class QuickScrapeRequest(BaseModel):
    company_id: str
    keyword_string: str
    platforms: Optional[str] = "tiktok,instagram,twitter,reddit,youtube"
    limit: Optional[int] = 100


@router.get(
    "/tunnel",
    summary="Get the active public tunnel URL (localtunnel or ngrok)",
)
async def get_tunnel_status():
    """
    Returns the currently active public tunnel URL used for Apify webhook callbacks.
    Also checks if ngrok is running locally on port 4040.
    """
    return await tm_get_tunnel_status()


@router.post(
    "/tunnel/start",
    summary="Start background ngrok or localtunnel automatically",
)
async def launch_tunnel(req: Optional[TunnelStartRequest] = None):
    """
    Automatically spins up ngrok or fallback localtunnel in the background and returns the detected URL.
    """
    port = req.port if req else 8000
    pref = req.preferred if req else "ngrok"
    res = await start_background_tunnel(port=port, preferred=pref)
    return res


@router.post(
    "/tunnel",
    summary="Update or set custom public tunnel/ngrok URL",
)
async def set_tunnel_url(req: TunnelUpdateRequest):
    """
    Allows setting or updating the public tunnel URL directly from the frontend UI.
    """
    url = req.tunnel_url.strip().rstrip('/')
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tunnel URL must start with http:// or https://"
        )
    saved_url = set_current_url(url)
    return {
        "status": "updated",
        "tunnel_url": saved_url
    }


def _build_actor_input(platform: str, keyword: str, limit: int = 100) -> dict:
    """Build the platform-specific input schema for each Apify actor with customizable limit (default 100)."""
    clean_keyword = keyword.replace("#", "").strip()
    is_hashtag = keyword.strip().startswith("#") and " " not in clean_keyword

    if platform == "tiktok":
        if is_hashtag:
            return {
                "hashtags": [clean_keyword],
                "resultsPerPage": limit,
                "maxItems": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            }
        else:
            return {
                "searchQueries": [clean_keyword],
                "resultsPerPage": limit,
                "maxItems": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            }
    elif platform == "instagram":
        tag = clean_keyword.replace(" ", "")
        direct_urls = [f"https://www.instagram.com/explore/tags/{tag}/"]
        if not is_hashtag:
            direct_urls.append(f"https://www.instagram.com/{tag}/")
        return {
            "search": clean_keyword,
            "searchType": "hashtag",
            "searchLimit": limit,
            "resultsType": "posts",
            "resultsLimit": limit,
            "maxComments": 25,
            "directUrls": direct_urls,
        }
    elif platform == "twitter":
        return {
            "searchTerms": [keyword],
            "maxTweets": limit,
            "maxItems": limit,
            "addUserInfo": True,
            "scrapeTweetReplies": True,
            "sort": "Latest",
        }
    elif platform == "reddit":
        return {
            "searches": [keyword],
            "maxItems": limit,
            "maxPostCount": limit,
            "sort": "new",
            "time": "year",
            "searchPosts": True,
            "searchComments": True,
            "skipComments": False,
        }
    elif platform == "youtube":
        return {
            "searchQueries": [clean_keyword],
            "maxComments": limit,
            "sort": "top",
        }
    return {}


async def _trigger_actor(
    platform: str,
    keyword_string: str,
    keyword_id: uuid.UUID,
    webhook_base_url: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """
    Launches a single Apify Actor run for the given platform + keyword.
    Sends actor input directly as top-level JSON body.
    Includes retry loop to prevent transient DNS or connection drops.
    """
    actor_id = APIFY_ACTORS[platform].replace("/", "~")
    token = settings.APIFY_API_TOKEN
    actor_input = _build_actor_input(platform, keyword_string, limit=limit)

    url = f"{APIFY_BASE}/acts/{actor_id}/runs?token={token}"
    if webhook_base_url and ("http://" in webhook_base_url or "https://" in webhook_base_url) and "localhost" not in webhook_base_url:
        import json, base64
        webhook_url = f"{webhook_base_url.rstrip('/')}/api/webhooks/apify?keyword_id={keyword_id}"
        webhook_cfg = [
            {
                "eventTypes": ["ACTOR.RUN.SUCCEEDED"],
                "requestUrl": webhook_url,
                "payloadTemplate": (
                    '{"eventData":{"defaultDatasetId":"{{defaultDatasetId}}",'
                    '"actorId":"{{actorId}}","actorRunId":"{{actorRunId}}"}}'
                ),
            }
        ]
        param = base64.b64encode(json.dumps(webhook_cfg).encode()).decode()
        url += f"&webhooks={param}"

    last_err = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, json=actor_input)
                if resp.status_code in (200, 201):
                    return resp.json().get("data", {})
                last_err = f"Apify returned {resp.status_code} for {platform}: {resp.text[:300]}"
            except Exception as e:
                last_err = str(e)
            if attempt < 2:
                await asyncio.sleep(1.5)

    raise RuntimeError(last_err or f"Failed to trigger {platform}")


async def _poll_and_ingest_run(run_id: str, keyword_id: uuid.UUID, platform: str):
    """
    Bulletproof automatic background poller:
    Monitors an Apify run until finished, then directly fetches the dataset
    and broadcasts to WebSockets. Does NOT require any public ngrok tunnel!
    """
    token = settings.APIFY_API_TOKEN
    max_wait_seconds = 180
    interval = 6
    elapsed = 0

    logger.info(f"⏳ Monitoring [{platform}] run {run_id} in background (auto-ingest poller active)...")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while elapsed < max_wait_seconds:
                await asyncio.sleep(interval)
                elapsed += interval
                try:
                    r = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}?token={token}")
                    if r.status_code == 200:
                        data = r.json().get("data", {})
                        run_status = data.get("status")
                        if run_status == "SUCCEEDED":
                            dataset_id = data.get("defaultDatasetId")
                            logger.info(f"🎉 Apify [{platform}] run {run_id} SUCCEEDED! Ingesting dataset {dataset_id}...")
                            if dataset_id:
                                await process_apify_dataset_task(dataset_id=dataset_id, keyword_id=keyword_id)
                            return
                        elif run_status in ("FAILED", "ABORTED", "TIMED-OUT"):
                            logger.warning(f"Apify [{platform}] run {run_id} ended with status: {run_status}")
                            return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error checking run {run_id} status: {e}")
    except asyncio.CancelledError:
        logger.info(f"🛑 Poller for [{platform}] run {run_id} cancelled.")
        raise
    finally:
        _ACTIVE_RUN_IDS.discard(run_id)
        if len(_ACTIVE_SCRAPE_TASKS) == 0 and len(_ACTIVE_RUN_IDS) == 0:
            _clear_active_scraping()
            try:
                from app.websocket_manager import ws_manager
                await ws_manager.broadcast_to_all({
                    "event": "SCRAPING_FINISHED",
                    "keyword_id": str(keyword_id)
                })
            except Exception:
                pass


# Concurrency Lock: Strictly only 1 keyword scrape allowed at any given time
_ACTIVE_SCRAPE_TASKS: set[asyncio.Task] = set()
_ACTIVE_RUN_IDS: set[str] = set()
_CURRENT_SCRAPING_INFO: Optional[dict] = None


def _check_and_lock_scraper(keyword_id: uuid.UUID, keyword_string: str):
    """Enforces that only 1 keyword can be scraped at a time."""
    global _CURRENT_SCRAPING_INFO
    if _CURRENT_SCRAPING_INFO is not None or len(_ACTIVE_SCRAPE_TASKS) > 0 or len(_ACTIVE_RUN_IDS) > 0:
        busy_kw = (_CURRENT_SCRAPING_INFO or {}).get("keyword_string", "another keyword")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A scrape is already active for keyword '{busy_kw}'. "
                f"You can only scrape 1 keyword at a time. "
                f"Please wait for it to complete or click 'Stop Scraper' before starting a new scrape."
            )
        )
    _CURRENT_SCRAPING_INFO = {
        "keyword_id": str(keyword_id),
        "keyword_string": keyword_string
    }


def _clear_active_scraping():
    global _CURRENT_SCRAPING_INFO
    _CURRENT_SCRAPING_INFO = None


@router.post(
    "/trigger/{keyword_id}",
    summary="Trigger live Apify scrape for requested platforms",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_scrape(
    keyword_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    webhook_base_url: Optional[str] = None,
    platforms: str = "tiktok,instagram,twitter,reddit,youtube",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """
    Triggers a live Apify scrape across requested platforms for the given keyword.
    Strictly permits only 1 keyword scrape at a time.
    """
    stmt = select(Keyword).where(Keyword.id == keyword_id, Keyword.is_active.is_(True))
    result = await db.execute(stmt)
    keyword = result.scalar_one_or_none()

    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active keyword {keyword_id} not found."
        )

    # 1. Enforce single-keyword scraping concurrency
    _check_and_lock_scraper(keyword.id, keyword.keyword_string)

    requested_platforms = [p.strip().lower() for p in platforms.split(",") if p.strip()]
    invalid = [p for p in requested_platforms if p not in APIFY_ACTORS]
    if invalid:
        _clear_active_scraping()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown platforms: {invalid}. Valid: {list(APIFY_ACTORS.keys())}"
        )

    if not settings.APIFY_API_TOKEN:
        _clear_active_scraping()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APIFY_API_TOKEN is not configured on the server."
        )

    active_webhook_url = (webhook_base_url or get_current_url() or "").strip()

    # Fire all platform scrapers concurrently in background with auto-ingestion
    background_tasks.add_task(
        _launch_all_platforms,
        keyword_string=keyword.keyword_string,
        keyword_id=keyword_id,
        webhook_base_url=active_webhook_url,
        requested_platforms=requested_platforms,
        limit=limit,
    )

    return {
        "status": "triggered",
        "keyword": keyword.keyword_string,
        "keyword_id": str(keyword_id),
        "platforms": requested_platforms,
        "limit": limit,
        "message": (
            f"Apify scrape launched for '{keyword.keyword_string}' across {len(requested_platforms)} platform(s). "
            f"Results will automatically be ingested and broadcast live to your dashboard via WebSockets."
        ),
    }


@router.post(
    "/quick-scrape",
    summary="Create or find keyword and immediately trigger live Apify scrape",
    status_code=status.HTTP_202_ACCEPTED,
)
async def quick_scrape(
    req: QuickScrapeRequest,
    background_tasks: BackgroundTasks,
    webhook_base_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Convenience endpoint: Ensures the keyword exists for this company,
    enforcing single-keyword scrape concurrency.
    """
    clean_kw = req.keyword_string.strip()
    if not clean_kw:
        raise HTTPException(status_code=400, detail="Keyword string cannot be empty.")

    # 1. Resolve company_id safely
    try:
        cid = uuid.UUID(str(req.company_id))
    except (ValueError, AttributeError):
        cid = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # 2. Create or retrieve existing keyword
    keyword_in = KeywordCreate(
        keyword_string=clean_kw,
        platform_flags=None
    )
    keyword = await create_keyword_for_company(db, cid, keyword_in)

    # 2. Enforce single-keyword scraping concurrency
    _check_and_lock_scraper(keyword.id, keyword.keyword_string)

    active_webhook_url = (webhook_base_url or get_current_url() or "").strip()
    requested_platforms = [p.strip().lower() for p in (req.platforms or "tiktok,instagram,twitter,reddit,youtube").split(",") if p.strip()]
    scrape_limit = req.limit or 100
    
    if not settings.APIFY_API_TOKEN:
        _clear_active_scraping()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APIFY_API_TOKEN is not configured on the server."
        )

    background_tasks.add_task(
        _launch_all_platforms,
        keyword_string=keyword.keyword_string,
        keyword_id=keyword.id,
        webhook_base_url=active_webhook_url,
        requested_platforms=requested_platforms,
        limit=scrape_limit,
    )

    return {
        "status": "triggered",
        "keyword": keyword.keyword_string,
        "keyword_id": str(keyword.id),
        "platforms": requested_platforms,
        "message": (
            f"Apify scrape launched for '{keyword.keyword_string}' across {len(requested_platforms)} platform(s). "
            f"Results will automatically be ingested and broadcast live to your dashboard via WebSockets."
        ),
    }


@router.get(
    "/status",
    summary="Check if scraping is actively running and get the current active keyword",
)
async def get_scraping_status():
    """Returns whether scraping is actively underway locally or in the cloud, including the current keyword."""
    local_active = len(_ACTIVE_SCRAPE_TASKS) > 0
    token = settings.APIFY_API_TOKEN
    cloud_running = 0

    if token:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(f"{APIFY_BASE}/actor-runs?token={token}&status=RUNNING&limit=10")
                if r.status_code == 200:
                    cloud_running = r.json().get("data", {}).get("total", 0)
        except Exception:
            pass

    is_active = local_active or cloud_running > 0 or len(_ACTIVE_RUN_IDS) > 0 or (_CURRENT_SCRAPING_INFO is not None)
    return {
        "is_scraping": is_active,
        "current_keyword": _CURRENT_SCRAPING_INFO,
        "local_tasks_count": len(_ACTIVE_SCRAPE_TASKS),
        "cloud_running_count": cloud_running,
        "tracked_run_ids": list(_ACTIVE_RUN_IDS)
    }


@router.post(
    "/stop",
    summary="Stop all active scraping runs and cancel background pollers",
)
async def stop_all_scraping():
    """
    Emergency Stop: Aborts all running or ready Apify actors in the cloud,
    and cancels all local background ingestion tasks immediately.
    """
    # 1. Cancel all active local background tasks
    cancelled_tasks = 0
    for task in list(_ACTIVE_SCRAPE_TASKS):
        if not task.done():
            task.cancel()
            cancelled_tasks += 1
    _ACTIVE_SCRAPE_TASKS.clear()

    # 2. Abort cloud runs on Apify
    token = settings.APIFY_API_TOKEN
    aborted_runs = []
    if token:
        async with httpx.AsyncClient(timeout=10.0) as client:
            runs_to_abort = set(_ACTIVE_RUN_IDS)
            # Also query Apify directly for any RUNNING or READY runs
            for st in ("RUNNING", "READY"):
                try:
                    r = await client.get(f"{APIFY_BASE}/actor-runs?token={token}&status={st}&limit=20")
                    if r.status_code == 200:
                        items = r.json().get("data", {}).get("items", [])
                        for item in items:
                            runs_to_abort.add(item.get("id"))
                except Exception as e:
                    logger.warning(f"Failed to query Apify runs with status {st}: {e}")

            # Send abort requests
            for run_id in runs_to_abort:
                if not run_id:
                    continue
                try:
                    abort_res = await client.post(f"{APIFY_BASE}/actor-runs/{run_id}/abort?token={token}")
                    if abort_res.status_code in (200, 201):
                        aborted_runs.append(run_id)
                        logger.info(f"🛑 Aborted Apify run {run_id}")
                    else:
                        logger.warning(f"Apify abort returned {abort_res.status_code} for {run_id}")
                except Exception as e:
                    logger.error(f"Failed to abort Apify run {run_id}: {e}")

    _ACTIVE_RUN_IDS.clear()
    _clear_active_scraping()

    try:
        from app.websocket_manager import ws_manager
        await ws_manager.broadcast_to_all({
            "event": "SCRAPING_STOPPED",
            "message": "Scraping has been stopped.",
            "aborted_runs_count": len(aborted_runs),
            "cancelled_tasks_count": cancelled_tasks
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast SCRAPING_STOPPED: {e}")

    logger.info(f"🛑 Stopped all scraping: {cancelled_tasks} tasks cancelled, {len(aborted_runs)} Apify cloud runs aborted.")
    return {
        "status": "stopped",
        "cancelled_tasks": cancelled_tasks,
        "aborted_runs": aborted_runs,
        "message": (
            f"Scraping stopped. {len(aborted_runs)} cloud scraper actor(s) aborted, "
            f"and {cancelled_tasks} background polling process(es) cancelled."
        )
    }


async def _launch_all_platforms(
    keyword_string: str,
    keyword_id: uuid.UUID,
    webhook_base_url: str,
    requested_platforms: list,
    limit: int = 100,
):
    """Background coroutine: launches Apify actors for each platform and starts auto-pollers."""
    current_task = asyncio.current_task()
    if current_task:
        _ACTIVE_SCRAPE_TASKS.add(current_task)

    try:
        from app.websocket_manager import ws_manager
        await ws_manager.broadcast_to_all({
            "event": "SCRAPING_STARTED",
            "keyword_string": keyword_string,
            "keyword_id": str(keyword_id)
        })
    except Exception:
        pass

    try:
        for platform in requested_platforms:
            try:
                run = await _trigger_actor(platform, keyword_string, keyword_id, webhook_base_url, limit=limit)
                run_id = run.get("id")
                if run_id:
                    _ACTIVE_RUN_IDS.add(run_id)
                logger.info(
                    f"✅ Apify [{platform}] actor started | "
                    f"keyword='{keyword_string}' | runId={run_id} | "
                    f"status={run.get('status')}"
                )
                if run_id:
                    # Spawn background poller to auto-ingest as soon as it completes
                    poller_task = asyncio.create_task(_poll_and_ingest_run(run_id, keyword_id, platform))
                    _ACTIVE_SCRAPE_TASKS.add(poller_task)
                    poller_task.add_done_callback(lambda t: _ACTIVE_SCRAPE_TASKS.discard(t))
            except Exception as e:
                logger.error(f"❌ Failed to trigger Apify [{platform}] for '{keyword_string}': {e}")
            # Brief delay to respect Apify account concurrency thresholds
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        logger.info(f"🛑 _launch_all_platforms cancelled for '{keyword_string}'")
        raise
    finally:
        if current_task:
            _ACTIVE_SCRAPE_TASKS.discard(current_task)
        if len(_ACTIVE_SCRAPE_TASKS) == 0 and len(_ACTIVE_RUN_IDS) == 0:
            _clear_active_scraping()
            try:
                from app.websocket_manager import ws_manager
                await ws_manager.broadcast_to_all({
                    "event": "SCRAPING_FINISHED",
                    "keyword_string": keyword_string
                })
            except Exception:
                pass


@router.get(
    "/actors",
    summary="List available Apify actors by platform",
)
async def list_actors():
    """Returns the Apify Actor IDs configured for each social media platform."""
    return {
        "apify_token_configured": bool(settings.APIFY_API_TOKEN),
        "actors": APIFY_ACTORS,
    }


@router.get(
    "/runs/{keyword_id}",
    summary="Check live Apify run statuses for a keyword",
)
async def get_run_status(keyword_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Fetches the most recent Apify runs across platforms from Apify's API.
    """
    stmt = select(Keyword).where(Keyword.id == keyword_id)
    result = await db.execute(stmt)
    keyword = result.scalar_one_or_none()

    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found.")

    if not settings.APIFY_API_TOKEN:
        return {"error": "APIFY_API_TOKEN not configured", "runs": []}

    runs_summary = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for platform, actor_id in APIFY_ACTORS.items():
            try:
                url = f"{APIFY_BASE}/acts/{actor_id}/runs?token={settings.APIFY_API_TOKEN}&limit=2"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json().get("data", {}).get("items", [])
                    runs_summary[platform] = [
                        {
                            "id": r.get("id"),
                            "status": r.get("status"),
                            "startedAt": r.get("startedAt"),
                            "finishedAt": r.get("finishedAt"),
                            "datasetId": r.get("defaultDatasetId"),
                        }
                        for r in data
                    ]
                else:
                    runs_summary[platform] = {"error": f"HTTP {resp.status_code}"}
            except Exception as e:
                runs_summary[platform] = {"error": str(e)}

    return {
        "keyword": keyword.keyword_string,
        "keyword_id": str(keyword_id),
        "runs": runs_summary,
    }

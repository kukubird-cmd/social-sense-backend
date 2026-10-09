import uuid
import httpx
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import settings
from app.models import ScrapedData
from app.services.sentiment_service import analyze_sentiment_claude

logger = logging.getLogger("apify_service")

# Commercial promo / sponsor detection patterns (detects brand-owned marketing copy)
PROMO_PATTERNS = [
    r"promo\s*code", r"discount", r"coupon", r"voucher", r"link\s*in\s*bio", 
    r"sponsored\s*by", r"collaboration\s*with", r"mytukar", r"carro\.co", 
    r"whatsapp\s*\+\d+", r"shopee\.com", r"lazada\.com", r"tiktok\s*shop", 
    r"dr\s*clear\s*aligners", r"使用优惠码", r"点击链接", r"合作诊所", r"赞助", 
    r"商务合作", r"官方购买", r"diskaun", r"kod\s*promo", r"pm\s*for\s*details",
    r"dm\s*for\s*collab", r"affiliate", r"official\s*store"
]


def check_is_owned_media(text: str, author: str, item: Dict[str, Any]) -> bool:
    """
    Detects if a post is self-promotional marketing copy or owned media published by the brand/creator.
    Viewer comments and external netizen reviews are always marked False.
    """
    # Direct flag from YouTube comment scrapers
    if item.get("authorIsChannelOwner") is True:
        return True
    if item.get("authorIsChannelOwner") is False:
        return False

    # Comments by third-party viewers are never owned media
    if (
        item.get("type") == "comment" 
        or "commentText" in item 
        or item.get("commentId") 
        or item.get("isReply")
        or item.get("post_type") == "comment"
    ):
        return False

    # Apify explicit sponsor / paid flags
    if item.get("isSponsored") is True or item.get("isAd") is True or item.get("isPaidContent") is True:
        return True

    # Video / post root items (channel video descriptions, creator feed posts) are creator-owned
    if "channelTitle" in item or "channelUsername" in item or item.get("post_type") == "post":
        return True

    text_lower = (text or "").lower()

    # Match against promotional / discount / affiliate patterns
    for pat in PROMO_PATTERNS:
        if re.search(pat, text_lower):
            return True

    return False


async def fetch_apify_dataset_items(dataset_id: str) -> List[Dict[str, Any]]:
    """Fetch raw scraped items from Apify's Dataset REST API."""
    token = (settings.APIFY_API_TOKEN or "").strip().replace("\r", "").replace("\n", "").replace('"', '').replace("'", "")
    if not token:
        logger.error("APIFY_API_TOKEN is not configured.")
        return []

    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    params = {"token": token, "limit": 1000}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                # Filter out empty placeholder/noResults objects or error objects
                return [d for d in data if not d.get("noResults") and not d.get("error")]
            return []
    except Exception as e:
        logger.error(f"Failed to fetch live Apify dataset {dataset_id}: {e}")
        return []


def parse_apify_record(item: Dict[str, Any], keyword_id: uuid.UUID) -> ScrapedData:
    """
    Normalizes multi-platform data payloads into standard ScrapedData:
    1. TikTok (Posts & Viewer Comments)
    2. Instagram (Posts, Reels & Comments)
    3. X / Twitter (Tweets & Replies)
    4. Reddit (Submissions & Comments)
    5. YouTube (Videos & Viewer Comments)
    """
    records = parse_apify_item_to_records(item, keyword_id)
    return records[0] if records else ScrapedData(
        keyword_id=keyword_id,
        platform="unknown",
        post_url="https://social-source.com",
        comment_text="",
        engagement_metrics={},
        sentiment_score="Neutral",
        is_owned_media=False,
        timestamp=datetime.utcnow()
    )


def parse_apify_item_to_records(item: Dict[str, Any], keyword_id: uuid.UUID) -> List[ScrapedData]:
    """
    Extracts ScrapedData from an Apify item.
    If the item contains nested viewer comments (e.g. YouTube video with comments array),
    both the root and all comments are extracted as distinct ScrapedData records.
    """
    results: List[ScrapedData] = []
    platform = "unknown"
    post_url = ""
    comment_text = ""
    author = "User"
    metrics: Dict[str, Any] = {}
    timestamp = datetime.utcnow()
    is_comment = False

    # --- 1. YOUTUBE COMMENT ITEM (blackfalcondata or streamers) ---
    if (
        item.get("commentId") 
        or item.get("datasetType") == "comment" 
        or "commentUrl" in item
        or ("channelUrl" in item and "videoUrl" in item and "text" in item)
        or (item.get("type") == "comment" and ("youtube" in str(item).lower() or "videoId" in item))
    ):
        platform = "youtube"
        is_comment = True
        post_url = item.get("videoUrl") or item.get("commentUrl") or item.get("url") or (f"https://www.youtube.com/watch?v={item.get('videoId')}" if item.get("videoId") else "")
        comment_text = item.get("text") or item.get("comment") or item.get("commentText") or ""
        author = item.get("author") or item.get("channelName") or "YouTube Viewer"
        is_owner = bool(item.get("authorIsChannelOwner", False))
        metrics = {
            "likes": item.get("likeCount", 0) or item.get("likes", 0) or item.get("voteCount", 0),
            "replies": item.get("replyCount", 0),
            "post_type": "comment",
            "author": author,
            "is_owned_media": is_owner
        }
        for ts_key in ("publishedAt", "publishedTime", "date"):
            if item.get(ts_key):
                try:
                    timestamp = datetime.fromisoformat(str(item[ts_key]).replace("Z", "+00:00"))
                    break
                except Exception:
                    pass

    # --- 2. YOUTUBE VIDEO ROOT ITEM ---
    elif "youtube.com" in item.get("url", "") or "channelTitle" in item or "videoId" in item:
        platform = "youtube"
        post_url = item.get("url") or f"https://www.youtube.com/watch?v={item.get('videoId', '')}"
        comment_text = item.get("title") or item.get("text") or ""
        author = item.get("channelUsername") or item.get("channelName") or item.get("channelTitle") or "Channel"
        metrics = {
            "likes": item.get("likeCount", 0) or item.get("likes", 0),
            "views": item.get("viewCount", 0),
            "comments": item.get("commentsCount", 0),
            "post_type": "post",
            "author": author,
            "is_owned_media": True
        }

        # Check for nested comments inside video object
        nested_comments = item.get("comments")
        if isinstance(nested_comments, list):
            for c in nested_comments:
                c_text = c.get("text") or c.get("commentText") or c.get("comment") or ""
                if not c_text.strip():
                    continue
                c_author = c.get("author") or c.get("channelName") or "Viewer"
                c_sentiment = analyze_sentiment_claude(c_text)
                results.append(ScrapedData(
                    keyword_id=keyword_id,
                    platform="youtube",
                    post_url=post_url,
                    comment_text=c_text.strip(),
                    engagement_metrics={
                        "likes": c.get("likes", 0) or c.get("likeCount", 0),
                        "post_type": "comment",
                        "author": c_author,
                        "is_owned_media": False
                    },
                    sentiment_score=c_sentiment,
                    is_owned_media=False,
                    timestamp=timestamp
                ))

    # --- 3. TIKTOK DETECTION ---
    elif "webVideoUrl" in item or "videoMeta" in item or "authorMeta" in item:
        platform = "tiktok"
        author_meta = item.get("authorMeta") or {}
        author = author_meta.get("name") or author_meta.get("nickName") or item.get("author") or "TikTok User"
        post_url = item.get("webVideoUrl") or f"https://www.tiktok.com/@{author}/video/{item.get('id', '')}"
        comment_text = item.get("text") or item.get("desc") or ""
        metrics = {
            "views": item.get("playCount", 0),
            "likes": item.get("diggCount", 0),
            "shares": item.get("shareCount", 0),
            "comments": item.get("commentCount", 0),
            "post_type": "post",
            "author": author
        }
    elif "commentText" in item and ("videoWebUrl" in item or "tiktok" in str(item).lower()):
        platform = "tiktok"
        is_comment = True
        post_url = item.get("videoWebUrl", "")
        comment_text = item.get("commentText") or item.get("text", "")
        author = item.get("user", {}).get("nickname") or item.get("author") or "TikTok Commenter"
        metrics = {
            "likes": item.get("diggCount", 0) or item.get("likesCount", 0),
            "replies": item.get("replyCommentTotal", 0),
            "post_type": "comment",
            "author": author
        }

    # --- 4. INSTAGRAM DETECTION ---
    elif "instagram" in item.get("url", "") or "shortCode" in item or ("caption" in item and "likesCount" in item):
        platform = "instagram"
        post_url = item.get("url") or f"https://www.instagram.com/p/{item.get('shortCode', '')}/"
        comment_text = item.get("caption") or item.get("text") or ""
        author = item.get("ownerUsername") or item.get("author") or "Instagram User"
        metrics = {
            "likes": item.get("likesCount", 0),
            "comments": item.get("commentsCount", 0),
            "views": item.get("videoViewCount", 0),
            "post_type": "post",
            "author": author,
            "is_owned_media": True
        }
        if "timestamp" in item and item["timestamp"]:
            try:
                timestamp = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00"))
            except Exception:
                pass

        # Check for firstComment or latestComments
        comments_arr = item.get("latestComments") or item.get("comments") or []
        for c in comments_arr:
            c_text = c.get("text") or c.get("comment") or ""
            if c_text.strip():
                c_author = c.get("ownerUsername") or c.get("author") or "IG Commenter"
                c_time = timestamp
                for c_ts in ("timestamp", "createdAt"):
                    if c.get(c_ts):
                        try:
                            c_time = datetime.fromisoformat(str(c[c_ts]).replace("Z", "+00:00"))
                            break
                        except Exception:
                            pass
                results.append(ScrapedData(
                    keyword_id=keyword_id,
                    platform="instagram",
                    post_url=post_url,
                    comment_text=c_text.strip(),
                    engagement_metrics={
                        "likes": c.get("likesCount", 0) or c.get("likes", 0),
                        "post_type": "comment",
                        "author": c_author,
                        "is_owned_media": False
                    },
                    sentiment_score=analyze_sentiment_claude(c_text),
                    is_owned_media=False,
                    timestamp=c_time
                ))

    # --- 5. X / TWITTER DETECTION ---
    elif "twitterUrl" in item or "tweetId" in item or "retweetCount" in item or "full_text" in item:
        platform = "twitter"
        author = item.get("user", {}).get("screen_name") or item.get("author") or "X User"
        post_url = item.get("twitterUrl") or item.get("url") or f"https://x.com/i/web/status/{item.get('id_str', item.get('id', ''))}"
        comment_text = item.get("full_text") or item.get("text") or ""
        is_reply = bool(item.get("in_reply_to_status_id") or item.get("inReplyToStatusId"))
        metrics = {
            "likes": item.get("favorite_count", 0) or item.get("likeCount", 0),
            "retweets": item.get("retweet_count", 0) or item.get("retweetCount", 0),
            "replies": item.get("reply_count", 0) or item.get("replyCount", 0),
            "post_type": "reply" if is_reply else "post",
            "author": f"@{author}" if not author.startswith("@") else author
        }

    # --- 6. REDDIT DETECTION ---
    elif "subreddit" in item or "reddit.com" in item.get("url", ""):
        platform = "reddit"
        post_url = item.get("url") or f"https://reddit.com{item.get('permalink', '')}"
        author = item.get("author") or item.get("username") or "u/Redditor"
        title = item.get("title", "")
        body = item.get("body", "") or item.get("selftext", "")
        comment_text = f"{title}\n{body}".strip() if title else body.strip()
        is_reddit_comment = item.get("dataType") == "comment" or bool(item.get("parentId"))
        metrics = {
            "upvotes": item.get("score", 0) or item.get("upvotes", 0),
            "comments": item.get("num_comments", 0) or item.get("numberOfComments", 0),
            "post_type": "comment" if is_reddit_comment else "post",
            "author": f"u/{author}" if not author.startswith("u/") else author
        }

    # Fallback
    else:
        post_url = item.get("url") or item.get("link") or "https://social-source.com"
        comment_text = item.get("text") or item.get("content") or item.get("body") or str(item)
        author = item.get("author") or "User"
        metrics = {"post_type": "post", "author": author}

    # Clean text
    clean_text = comment_text.strip()
    if not clean_text and "title" in item:
        clean_text = item["title"]

    if not clean_text:
        return results

    # Determine owned media vs viewer comment
    is_owned = False
    if is_comment:
        is_owned = bool(item.get("authorIsChannelOwner", False))
    else:
        is_owned = check_is_owned_media(clean_text, author, item)

    metrics["is_owned_media"] = is_owned
    metrics["author"] = author

    # Sentiment analysis with new multilingual engine
    sentiment = analyze_sentiment_claude(clean_text)

    # Parse ISO timestamp if available
    for ts_field in ("publishedAt", "createTimeISO", "createdAt", "date", "publishedTime", "timestamp"):
        if ts_field in item and item[ts_field]:
            try:
                timestamp = datetime.fromisoformat(str(item[ts_field]).replace("Z", "+00:00"))
                break
            except Exception:
                pass

    primary_record = ScrapedData(
        keyword_id=keyword_id,
        platform=platform,
        post_url=post_url,
        comment_text=clean_text,
        engagement_metrics=metrics,
        sentiment_score=sentiment,
        is_owned_media=is_owned,
        timestamp=timestamp
    )

    results.insert(0, primary_record)
    return results

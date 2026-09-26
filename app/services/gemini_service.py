import json
import logging
import html
from typing import List, Dict, Any, Optional
import httpx
from app.config import settings

logger = logging.getLogger("gemini_service")

# Candidate models in order of priority (with automatic fallback)
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest"
]

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


import codecs
import re

def unescape_unicode(s: str) -> str:
    """Decodes raw \\uXXXX unicode escapes and UTF-16 surrogate pairs into genuine UTF-8 characters."""
    if not s:
        return ""
    try:
        # Decode UTF-16 surrogate pairs first (e.g. \\ud83c\\udfa5 -> 🎥)
        s = re.sub(
            r'\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}',
            lambda m: codecs.decode(m.group(0), 'unicode_escape').encode('utf-16', 'surrogatepass').decode('utf-16'),
            s
        )
        # Decode standard 4-digit unicode escapes (e.g. \\u534e -> 华)
        s = re.sub(r'\\u[0-9a-fA-F]{4}', lambda m: codecs.decode(m.group(0), 'unicode_escape'), s)
    except Exception:
        pass
    return s


def linkify_citations(reply: str, records: List[Dict[str, Any]]) -> str:
    """
    Scans AI reply for bare platform citations like (YouTube), (Reddit), (TikTok), (Instagram), (X),
    and resolves them against scraped database records to guarantee genuine, clickable Markdown links.
    """
    if not reply or not records:
        return reply

    platform_names = {
        "youtube": "YouTube",
        "reddit": "Reddit",
        "tiktok": "TikTok",
        "instagram": "Instagram",
        "twitter": "X/Twitter",
        "x": "X/Twitter"
    }

    # Group records by platform
    records_by_platform: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        p = (r.get("platform") or "unknown").lower()
        if p == "twitter":
            p = "x"
        records_by_platform.setdefault(p, []).append(r)

    # Pattern: (YouTube), (Reddit), etc. NOT preceded by ']'
    citation_pattern = re.compile(
        r'(?<!\])\s*\((YouTube|Reddit|TikTok|Instagram|Twitter|X|X\/Twitter)\)',
        re.IGNORECASE
    )

    lines = reply.split("\n")
    processed_lines = []

    for line in lines:
        match = citation_pattern.search(line)
        if match:
            cited_platform = match.group(1).lower()
            norm_platform = "x" if cited_platform in ("x/twitter", "twitter") else cited_platform
            display_platform = platform_names.get(norm_platform, match.group(1))

            candidate_records = records_by_platform.get(norm_platform, [])
            best_match = None
            highest_score = 0.0

            # 1. Search for quoted text in the line
            quotes = re.findall(r'["\u201c\u201d\u2018\u2019*]([^"\u201c\u201d\u2018\u2019*]{6,})["\u201c\u201d\u2018\u2019*]', line)
            for rec in candidate_records:
                rec_url = rec.get("post_url") or rec.get("url") or ""
                if not rec_url:
                    continue

                content = (rec.get("comment_text") or rec.get("content") or "").lower()
                for q in quotes:
                    q_clean = q.strip().lower()
                    if q_clean in content or content in q_clean:
                        best_match = rec
                        highest_score = 10.0
                        break
                if highest_score >= 10.0:
                    break

                # 2. Token overlap score
                line_lower = line.lower()
                words = [w for w in re.findall(r'\b\w{4,}\b', content) if w not in ("http", "https", "view", "post", "video")]
                matches_count = sum(1 for w in words if w in line_lower)
                score = matches_count / max(len(words), 1)
                if score > highest_score:
                    highest_score = score
                    best_match = rec

            if not best_match and candidate_records:
                best_match = candidate_records[0]

            if best_match:
                rec_url = best_match.get("post_url") or best_match.get("url") or ""
                if rec_url:
                    replacement = f" [View on {display_platform}]({rec_url})"
                    line = citation_pattern.sub(replacement, line, count=1)

        processed_lines.append(line)

    return "\n".join(processed_lines)


def clean_text(raw_text: str) -> str:
    """Decodes unicode escapes, HTML entities, and normalizes whitespace."""
    if not raw_text:
        return ""
    # First decode any unicode escape sequences
    cleaned = unescape_unicode(raw_text)
    # Unescape HTML entities like &quot;, &#39;, &amp;
    cleaned = html.unescape(cleaned)
    # Remove redundant whitespace
    return " ".join(cleaned.split())


async def call_gemini_api(prompt: str, system_instruction: Optional[str] = None) -> str:
    """Calls Gemini REST API with automatic model fallbacks, high token limits, and multipart handling."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured in backend/.env")

    last_error = None
    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in GEMINI_MODELS:
            url = f"{BASE_URL}/{model}:generateContent?key={api_key}"
            
            # Use maxOutputTokens: 65536 to ensure thinking tokens never starve the response
            payload_attempts = [
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 65536,
                        "thinkingConfig": {"thinkingBudget": 0}
                    }
                },
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 65536
                    }
                }
            ]

            for payload_idx, payload in enumerate(payload_attempts):
                if system_instruction:
                    payload["systemInstruction"] = {
                        "parts": [{"text": system_instruction}]
                    }

                try:
                    response = await client.post(url, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get("candidates", [])
                        if candidates and "content" in candidates[0]:
                            finish_reason = candidates[0].get("finishReason", "")
                            parts = candidates[0]["content"].get("parts", [])
                            
                            # Concatenate ALL non-thought text parts to avoid dropping content
                            text_parts = [
                                p.get("text", "")
                                for p in parts
                                if "text" in p and not p.get("thought", False)
                            ]
                            full_text = "".join(text_parts).strip()

                            if finish_reason == "MAX_TOKENS":
                                logger.warning(f"Model {model} hit MAX_TOKENS limit (length: {len(full_text)})")
                                # If truncated and we have another payload attempt, try next attempt
                                if payload_idx + 1 < len(payload_attempts):
                                    continue

                            if full_text:
                                return full_text

                    elif response.status_code in (404, 503, 429):
                        logger.warning(f"Model {model} returned HTTP {response.status_code}. Falling back...")
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                        break # Try next model
                    elif response.status_code == 400 and "thinkingConfig" in str(payload):
                        # Some models may reject thinkingConfig, try payload without it
                        continue
                    else:
                        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                        logger.warning(f"Gemini error with {model}: {last_error}")
                        break
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Failed calling {model}: {e}")
                    break

    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


async def chat_with_scraped_data(
    user_query: str,
    scraped_records: List[Dict[str, Any]],
    keyword_filter: Optional[str] = None,
    topic_context: Optional[str] = None
) -> Dict[str, Any]:
    """
    Answers user queries grounded strictly on the database of scraped social media posts,
    with topic disambiguation and precision filtering.
    """
    # Prioritize genuine viewer comments & earned media; deprioritize self-promotional owned posts
    earned_records = [r for r in scraped_records if not r.get("is_owned_media")]
    owned_records = [r for r in scraped_records if r.get("is_owned_media")]
    
    # Send up to 70 real viewer comments and at most 10 owned posts for background context
    prioritized_records = earned_records[:70] + owned_records[:10] if earned_records else scraped_records[:70]

    cleaned_records = []
    for r in prioritized_records:
        text = clean_text(r.get("comment_text", ""))
        platform = r.get("platform", "unknown")
        sentiment = r.get("sentiment_score", "Neutral")
        metrics = r.get("engagement_metrics") or {}
        author = r.get("author") or metrics.get("author") or "User"
        is_owned = bool(r.get("is_owned_media") or metrics.get("is_owned_media", False))
        raw_ts = str(r.get("timestamp") or "")
        orig_date = metrics.get("publishedTimeText") or metrics.get("publishedAt") or (raw_ts[:10] if raw_ts else "Recent")
        cleaned_records.append({
            "platform": platform,
            "sentiment": sentiment,
            "media_type": "Owned Promotional Copy" if is_owned else "Viewer/User Comment",
            "author": author,
            "date": str(orig_date)[:25],
            "content": text[:350],
            "metrics": metrics,
            "url": r.get("post_url", "")
        })

    records_json = json.dumps(cleaned_records, indent=2, ensure_ascii=False)

    context_guidance = ""
    if topic_context and topic_context.strip():
        context_guidance = (
            f"\nCRITICAL TARGET FOCUS: The user has specified that the keyword '{keyword_filter or 'tracked keyword'}' refers specifically to: "
            f"'{topic_context.strip()}'. Strictly focus your analysis ONLY on posts relevant to this entity/subject. "
            f"Disregard, exclude, or explicitly identify and filter out any posts about unrelated topics, homonyms, or coincidental acronyms."
        )
    else:
        context_guidance = (
            f"\nTOPIC DISAMBIGUATION: The keyword '{keyword_filter or 'monitored keyword'}' may have multiple different meanings, "
            f"entities, or acronyms in the data (e.g. acronym collisions or unrelated slang). "
            f"If you detect 2 or more distinct topics or entities in the scraped data, list 2 to 4 distinct topic choices at the very end of your response formatted EXACTLY like this:\n"
            f"TOPIC_OPTIONS: [\"Topic 1 (Context/Location)\", \"Topic 2 (Context/Location)\"]\n"
            f"This enables the user to click a topic pill and immediately filter the analysis."
        )

    system_instruction = (
        "You are an enterprise-grade Social Media & Market Research AI Analyst for brand intelligence. "
        "You analyze real social data from TikTok, Reddit, Instagram, X/Twitter, and YouTube for corporate executives, brand managers, and PR teams.\n\n"
        "CORE CORPORATE INTELLIGENCE PRINCIPLES:\n"
        "1. STRICT AUTO-NEGLECT OF OWNED MEDIA: The primary purpose of this software is to emphasize sentiments and comments by REAL USERS and VIEWERS. Auto-neglect and disregard self-promotional marketing copy, sponsor disclaimers, coupon codes, and promotional captions published by the brand/creator themselves (marked as 'Owned Promotional Copy'). NEVER mistake marketing captions for public praise! Base all sentiment scores, praise, grievances, and criticism strictly on genuine viewer comments ('Viewer/User Comment').\n"
        "2. UNVARNISHED SENTIMENT & NEGATIVE SIGNALS: Do not sugarcoat. Actively identify criticisms, complaints, negative reactions, controversy, skepticism, and dissatisfaction expressed by viewers. Assign a clear Crisis Risk Level: [LOW / MODERATE / ELEVATED / SEVERE].\n"
        "3. RECENCY & TIMELINE ANALYSIS: Always evaluate post dates ('date' field). Tell the company what happened recently vs historically.\n"
        "4. WHAT CUSTOMERS REALLY CARE ABOUT: Extract genuine customer praise, pain points, objections, and inquiries from the comments.\n"
        "5. CLICKABLE LINKS ONLY: When citing evidence or quotes, you MUST provide a direct clickable Markdown link: [View on Platform](url) (e.g. [View on YouTube](https://...), [View on Reddit](https://...), [View on TikTok](https://...), [View on Instagram](https://...), [View on X](https://...)). NEVER output bare parenthetical names like '(YouTube)' or '(Reddit)'.\n"
        "6. NO RAW UNICODE ESCAPES: Always output genuine UTF-8 text and clean formatting.\n"
        "7. STRICT PLATFORM INTEGRITY: If the user specifically asks about a particular platform (e.g. 'on ig', 'on instagram', 'on youtube', 'on tiktok'), evaluate ONLY posts from that platform. Output clickable links ONLY for that requested platform. Never substitute YouTube links when the user asked about Instagram or TikTok.\n"
        f"8. STRICT KEYWORD SCOPE: All analysis, sentiments, and quotes must strictly reflect the target keyword '{keyword_filter or 'monitored keyword'}'. Never confuse, blend, or infer sentiments from unrelated brands or other monitored keywords.\n"
        + context_guidance
    )

    prompt = f"""Here is the database of recent scraped social media records (Total: {len(scraped_records)} available, sample of {len(cleaned_records)} provided below):
{records_json}

Active Keyword Filter: {keyword_filter or 'All Tracked Keywords'}
{f'Target Topic Focus: {topic_context}' if topic_context else ''}

User Query / Request:
{user_query}

INSTRUCTIONS FOR YOUR RESPONSE:
1. DIRECTLY & SPECIFICALLY ANSWER THE USER'S QUESTION:
   - Do NOT dump a rigid, repetitive boilerplate template unless the user explicitly asked for a "full formal report" or "complete executive audit".
   - If the user asks a specific question (e.g. about a specific platform, top complaints, sentiment, recent drama, or recommendations), ANSWER THAT SPECIFIC QUESTION DIRECTLY with sharp, high-value intelligence.
   - Write like a senior brand strategist / PR director: crisp, insightful, quantitative where helpful, and with zero generic filler.
2. GROUNDING WITH REAL EVIDENCE & CITATIONS:
   - Quote specific viewer comments from the data as proof and provide direct clickable links: [View on Platform](url).
   - Never mistake creator/brand self-promotions ('Owned Promotional Copy') for public opinion. Focus strictly on genuine viewer reactions.
3. CONVERSATIONAL & ACTION-ORIENTED:
   - Provide concrete takeaways, real viewer sentiment drivers, and clear next steps that give the client immediate business value.
"""

    reply = await call_gemini_api(prompt, system_instruction=system_instruction)

    # 1. Decode any remaining raw unicode escapes
    reply = unescape_unicode(reply)

    # 2. Automatically linkify any citations that Gemini left as (YouTube), (Reddit), etc.
    reply = linkify_citations(reply, cleaned_records)

    # Extract structured topic options if present (supports JSON, Python list, and fallback)
    import re
    import ast
    topic_options = []
    options_match = re.search(r"(?:[-#*=\s]*TOPIC[-_\s]*OPTIONS[-#*=\s]*:?\s*)(\[[^\]]+\])", reply, re.IGNORECASE)
    if options_match:
        raw_list = options_match.group(1).strip()
        try:
            topic_options = json.loads(raw_list)
        except Exception:
            try:
                parsed = ast.literal_eval(raw_list)
                if isinstance(parsed, list):
                    topic_options = [str(x).strip() for x in parsed]
            except Exception:
                topic_options = [s.strip() for s in re.findall(r"['\"]([^'\"]+)['\"]", raw_list) if s.strip()]

        # Clean the raw TOPIC_OPTIONS marker out of user-visible text
        reply = re.sub(r"(?:[-#*=\s]*TOPIC[-_\s]*OPTIONS[-#*=\s]*:?\s*)\[[^\]]+\]", "", reply, flags=re.IGNORECASE).strip()

    return {
        "reply": reply,
        "referenced_records_count": len(cleaned_records),
        "total_records_available": len(scraped_records),
        "topic_options": topic_options
    }


async def translate_and_summarize_post(raw_text: str, keyword: str) -> Dict[str, Any]:
    """
    Cleans raw comment, detects language, translates to English, and evaluates genuine sentiment.
    """
    cleaned = clean_text(raw_text)
    if not cleaned:
        return {
            "english_translation": "",
            "original_language": "Unknown",
            "sentiment": "Neutral",
            "is_relevant": False,
            "summary": ""
        }

    prompt = f"""Analyze this social media post collected for the monitored keyword/topic: "{keyword}".

Post Text:
\"\"\"{cleaned}\"\"\"

Respond ONLY with a valid JSON object matching this schema:
{{
  "english_translation": "Fluent, clean English translation (or original text if already English)",
  "original_language": "Detected language name (e.g. English, Malay, Chinese, etc.)",
  "sentiment": "Positive" | "Neutral" | "Negative" | "Crisis",
  "is_relevant": true | false,
  "summary": "1 concise sentence explaining the user's point"
}}
"""
    try:
        raw_res = await call_gemini_api(prompt)
        json_str = raw_res.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()
        return json.loads(json_str)
    except Exception as e:
        logger.error(f"Failed to translate/analyze post with Gemini: {e}")
        return {
            "english_translation": cleaned,
            "original_language": "English",
            "sentiment": "Neutral",
            "is_relevant": True,
            "summary": cleaned[:100]
        }

import re
import logging
from typing import Literal, List, Optional
from app.config import settings

logger = logging.getLogger("sentiment_service")

SentimentType = Literal["Positive", "Neutral", "Negative", "Crisis"]

# ---------------------------------------------------------------------------
# Multilingual Lexicons (English, Chinese, Malay & Manglish)
# ---------------------------------------------------------------------------

CRISIS_KEYWORDS = {
    # English
    "boycott", "scam", "fraud", "lawsuit", "sue", "illegal", "defective", 
    "toxic", "poison", "hospital", "injury", "danger", "exploded", 
    "corrupt", "investigate", "fda", "pdpa", "police", "arrest", "court",
    # Chinese (Simplified & Traditional)
    "诈骗", "报警", "封杀", "犯法", "违法", "坐牢", "欺诈", "黑心", "勒索", 
    "中毒", "医院", "受伤", "爆炸", "贪污", "调查", "起诉", "告上法庭", "抵制",
    # Malay
    "boikot", "scam", "penipu", "polis", "mahkamah", "tangkap", "jenayah", 
    "bahaya", "racun", "hospital", "cedera", "meletup", "saman"
}

NEGATIVE_KEYWORDS = {
    # English
    "terrible", "horrible", "worst", "broken", "glitch", "failed", "sucks", 
    "slow", "hate", "trash", "bad", "disappointed", "unacceptable", 
    "annoying", "overpriced", "useless", "regret", "waste", "breakout",
    "overrated", "boring", "cringe", "unfunny", "sellout", "clickbait", 
    "dishonest", "poor quality", "bad service", "fake", "lies", "lying",
    "disgusting", "rip off", "waste of money", "waste of time", "rude",
    # Chinese (Simplified & Traditional)
    "难看", "难吃", "难用", "难听", "翻车", "很贵", "太贵", "骗人", "骗钱", 
    "退步", "广告太多", "尴尬", "失望", "不推荐", "差评", "避雷", "塌房", 
    "恶心", "敷衍", "变质", "浪费钱", "浪费时间", "无聊", "变了", "做作", 
    "讨厌", "差劲", "垃圾", "坑人", "劝退", "假", "做作", "无语", "烦人", 
    "变味", "商业化", "恰饭", "烂", "差", "恶劣", "敷衍了事", "毫无意义",
    # Malay & Manglish
    "bosan", "mahal", "tak best", "tak sedap", "tak guna", "teruk", "rugi", 
    "kecewa", "hampa", "kualiti rendah", "koyak", "bazir", "lembap", 
    "menyampah", "babi", "bodoh", "sial", "rosak", "hampeh", "mengarut", 
    "fake gila", "tak puas hati", "kureng", "sampah"
}

POSITIVE_KEYWORDS = {
    # English
    "love", "best", "amazing", "great", "excellent", "awesome", "recommend", 
    "fire", "smooth", "clean", "perfect", "worth it", "obsessed", "favorite", 
    "fantastic", "brilliant", "superb", "helpful", "gem", "underrated", 
    "wholesome", "legend", "goat", "masterpiece", "support", "proud", "congrats",
    # Chinese (Simplified & Traditional)
    "好看", "好吃", "好用", "好听", "喜欢", "支持", "厉害", "赞", "棒", 
    "推荐", "优秀", "感动", "真实", "有趣", "搞笑", "期待", "值得", "佩服", 
    "榜样", "超赞", "太棒了", "正能量", "加油", "精彩", "良心", "敬佩", 
    "高质量", "用心", "绝了", "神作", "佩服佩服", "好可爱", "温暖",
    # Malay
    "terbaik", "mantap", "padu", "suka", "sokong", "hebat", "bagus", 
    "lawa", "sedap", "berkualiti", "berbaloi", "terharu", "kelakar", 
    "gempak", "power", "tahniah", "respect", "padu beb", "cantik"
}


def analyze_sentiment_claude(text: str) -> SentimentType:
    """
    Multilingual heuristic & keyword sentiment classifier.
    Handles English, Chinese (Simplified/Traditional), Malay, and Manglish.
    """
    if not text or not text.strip():
        return "Neutral"

    clean_text = text.lower().strip()
    
    # Extract English/alphanumeric tokens
    en_words = set(re.findall(r"\b\w+\b", clean_text))

    # 1. Immediate Crisis Check
    for k in CRISIS_KEYWORDS:
        if k in en_words or k in clean_text:
            return "Crisis"

    # Multi-word crisis patterns
    if any(phrase in clean_text for phrase in [
        "file a lawsuit", "boycott this", "calling the police", 
        "take to court", "lapor polis", "nak saman", "tuntut ganti rugi"
    ]):
        return "Crisis"

    # 2. Score negative and positive signals
    neg_score = 0
    pos_score = 0

    for k in NEGATIVE_KEYWORDS:
        if k in en_words or k in clean_text:
            neg_score += 1

    for k in POSITIVE_KEYWORDS:
        if k in en_words or k in clean_text:
            pos_score += 1

    # Strong negative modifiers or patterns
    if any(phrase in clean_text for phrase in [
        "not worth", "waste of", "don't buy", "do not buy", "never again",
        "stop watching", "unsubscribed", "tak berbaloi", "jangan beli",
        "jangan tengok", "tidak digalakkan", "越来越差", "再也不看", "坚决不买",
        "根本就是骗", "毫无诚意", "令人失望"
    ]):
        neg_score += 2

    if neg_score > pos_score:
        return "Negative"
    elif pos_score > neg_score:
        return "Positive"
    elif pos_score > 0 and pos_score == neg_score:
        return "Neutral"

    return "Neutral"


async def batch_classify_sentiment_gemini(records: List[dict]) -> List[SentimentType]:
    """
    AI-powered batch classifier using Gemini Flash for nuance, sarcasm, and multilingual depth.
    Falls back to `analyze_sentiment_claude` if Gemini call is unavailable or fails.
    """
    if not records:
        return []

    # If no Gemini API key, use the local multilingual heuristic
    if not settings.GEMINI_API_KEY:
        return [analyze_sentiment_claude(r.get("text", "")) for r in records]

    try:
        from app.services.gemini_service import call_gemini_api
        import json

        # Prepare a lightweight batch
        items_payload = []
        for idx, r in enumerate(records[:40]):
            t = (r.get("text") or "")[:200].strip()
            items_payload.append({"id": idx, "text": t})

        prompt = (
            "Classify the sentiment of each social media post or viewer comment below.\n"
            "Options for sentiment: 'Positive', 'Neutral', 'Negative', 'Crisis'.\n"
            "- 'Negative': complaints, criticism, dissatisfaction, sarcasm, annoyance, poor quality, bad experience, disappointment, sellout, too many ads.\n"
            "- 'Crisis': severe brand risks, illegal acts, boycotts, lawsuits, food safety, scams.\n"
            "- 'Positive': genuine praise, appreciation, excitement, liking the creator/brand.\n"
            "- 'Neutral': questions, casual chatter, general mentions, news reports.\n"
            "Languages: English, Chinese (Simplified/Traditional), Malay, Manglish.\n\n"
            f"Input:\n{json.dumps(items_payload, ensure_ascii=False)}\n\n"
            "Output strictly a JSON list of objects: [{\"id\": 0, \"sentiment\": \"...\"}, ...]"
        )

        resp_text = await call_gemini_api(prompt, system_instruction="You are an expert multilingual social media sentiment classifier.")
        
        # Parse JSON
        match = re.search(r"\[.*\]", resp_text, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            sentiment_map = {item["id"]: item["sentiment"] for item in parsed if "id" in item and "sentiment" in item}
            results = []
            for idx, r in enumerate(records):
                s = sentiment_map.get(idx)
                if s in ("Positive", "Neutral", "Negative", "Crisis"):
                    results.append(s)
                else:
                    results.append(analyze_sentiment_claude(r.get("text", "")))
            return results
    except Exception as e:
        logger.warning(f"Gemini batch sentiment classification fallback: {e}")

    # Fallback to local heuristic
    return [analyze_sentiment_claude(r.get("text", "")) for r in records]

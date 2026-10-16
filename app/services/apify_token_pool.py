"""
Apify Token Pool & Automatic Quota Failover Service.
Manages multiple Apify API tokens (primary + backups).
Automatically detects quota/credit exhaustion (402 Payment Required, rate limits, usage limits)
and seamlessly fails over to backup tokens so scraping continues without interruption.
"""
import os
import logging
from typing import List, Optional, Set, Dict, Any
from app.config import settings

logger = logging.getLogger("apify_token_pool")


def sanitize_token(token: Optional[str]) -> Optional[str]:
    """Strips whitespace, surrounding quotes, and line breaks from an API token."""
    if not token or not isinstance(token, str):
        return None
    cleaned = token.strip().replace("\r", "").replace("\n", "").replace('"', '').replace("'", "")
    return cleaned if cleaned else None


class ApifyTokenPool:
    """Thread-safe Apify token manager with automatic quota rotation."""

    def __init__(self):
        self._exhausted_tokens: Set[str] = set()
        self._exhaustion_reasons: Dict[str, str] = {}

    def get_all_configured_tokens(self) -> List[str]:
        """Returns all uniquely configured Apify API tokens in priority order (primary first, then backups)."""
        tokens: List[str] = []

        # 1. Primary token
        primary_raw = settings.APIFY_API_TOKEN or os.getenv("APIFY_API_TOKEN", "")
        if primary_raw:
            for part in primary_raw.replace(";", ",").split(","):
                t = sanitize_token(part)
                if t and t not in tokens:
                    tokens.append(t)

        # 2. Dedicated backup token
        backup_raw = settings.APIFY_BACKUP_API_TOKEN or os.getenv("APIFY_BACKUP_API_TOKEN", "") or os.getenv("APIFY_API_TOKEN_BACKUP", "")
        if backup_raw:
            for part in backup_raw.replace(";", ",").split(","):
                t = sanitize_token(part)
                if t and t not in tokens:
                    tokens.append(t)

        # 3. Comma/semicolon delimited list of tokens
        multi_raw = settings.APIFY_API_TOKENS or os.getenv("APIFY_API_TOKENS", "")
        if multi_raw:
            for part in multi_raw.replace(";", ",").split(","):
                t = sanitize_token(part)
                if t and t not in tokens:
                    tokens.append(t)

        return tokens

    def get_active_token(self) -> Optional[str]:
        """Returns the first non-exhausted token in the pool, or the primary if all are marked exhausted."""
        tokens = self.get_all_configured_tokens()
        if not tokens:
            return None

        # Look for first non-exhausted
        for t in tokens:
            if t not in self._exhausted_tokens:
                return t

        # If all exhausted, fallback to primary token as last resort
        logger.warning("All Apify tokens in pool are currently marked exhausted! Falling back to primary token.")
        return tokens[0]

    def get_candidate_tokens(self) -> List[str]:
        """Returns ordered list of tokens to try: non-exhausted tokens first, followed by exhausted ones."""
        tokens = self.get_all_configured_tokens()
        non_exhausted = [t for t in tokens if t not in self._exhausted_tokens]
        exhausted = [t for t in tokens if t in self._exhausted_tokens]
        return non_exhausted + exhausted

    def mark_exhausted(self, token: str, reason: str = ""):
        """Flags a token as exhausted and switches future calls to the next available backup token."""
        token_clean = sanitize_token(token)
        if not token_clean:
            return
        self._exhausted_tokens.add(token_clean)
        self._exhaustion_reasons[token_clean] = reason

        tokens = self.get_all_configured_tokens()
        masked = f"{token_clean[:10]}...{token_clean[-4:]}" if len(token_clean) > 14 else "***"
        next_tok = self.get_active_token()
        next_masked = f"{next_tok[:10]}...{next_tok[-4:]}" if (next_tok and len(next_tok) > 14) else "None"

        logger.warning(
            f"⚠️ Apify Token [{masked}] marked EXHAUSTED / QUOTA REACHED (Reason: {reason}). "
            f"Active pool failover -> Next token: [{next_masked}] (Total configured tokens: {len(tokens)})"
        )

    def is_quota_or_billing_error(self, status_code: int, response_text: str = "") -> bool:
        """Determines if an HTTP error indicates exhausted quota, payment required, or compute unit limits."""
        if status_code in (402, 429):
            return True

        text_lower = (response_text or "").lower()
        quota_keywords = [
            "payment required",
            "usage limit",
            "monthly usage",
            "compute units",
            "out of compute units",
            "not enough compute units",
            "exceeded limit",
            "exceeded monthly usage",
            "rate limit",
            "credit limit",
            "insufficient credit",
            "plan limit",
            "upgrade your plan"
        ]
        if status_code in (400, 401, 403, 409):
            if any(k in text_lower for k in quota_keywords):
                return True
        return False

    def reset_exhausted(self):
        """Clears all exhausted flags (e.g. after adding funds or token renewal)."""
        self._exhausted_tokens.clear()
        self._exhaustion_reasons.clear()
        logger.info("Cleared exhausted flags for all Apify tokens in pool.")

    def get_pool_status(self) -> Dict[str, Any]:
        """Returns structured JSON summarizing all tokens in the pool."""
        tokens = self.get_all_configured_tokens()
        active = self.get_active_token()
        token_list = []
        for idx, t in enumerate(tokens):
            masked = f"{t[:10]}...{t[-4:]}" if len(t) > 14 else f"token_{idx+1}"
            is_exhausted = t in self._exhausted_tokens
            token_list.append({
                "index": idx + 1,
                "masked": masked,
                "is_active": (t == active and not is_exhausted),
                "is_exhausted": is_exhausted,
                "exhaustion_reason": self._exhaustion_reasons.get(t, None),
                "role": "primary" if idx == 0 else f"backup_{idx}"
            })
        return {
            "total_tokens": len(tokens),
            "active_token": f"{active[:10]}...{active[-4:]}" if (active and len(active) > 14) else None,
            "has_backup": len(tokens) > 1,
            "tokens": token_list
        }


token_pool = ApifyTokenPool()

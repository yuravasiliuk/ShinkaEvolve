"""Claude subscription usage gauge for headless (subscription-backed) runs.

Claude Code's ``/usage`` view is backed by an OAuth endpoint. Reading the
local Claude Code credentials lets a run ask "how much of the 5-hour /
weekly window is used?" *before* dispatching mutations, so it can pause
until the window resets instead of burning patch attempts on calls that
are guaranteed to fail.

The endpoint is unofficial (it is what the Claude Code client itself
queries), so every failure mode degrades to ``None`` and gating simply
switches off.

Module-level "limit hit" state is shared between the headless provider
(which detects limit errors reactively and records them) and the runner
(which resolves the pause deadline and clears the state once it passes).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS_PATH = Path("~/.claude/.credentials.json")
_OAUTH_BETA = "oauth-2025-04-20"
_REQUEST_TIMEOUT = 15.0

_STATE_LOCK = threading.Lock()
_USAGE_CACHE: Optional["SubscriptionUsage"] = None
_LIMIT_HIT: Optional["LimitHit"] = None


class SubscriptionLimitError(RuntimeError):
    """A subscription-backed call failed because the usage window is exhausted."""

    def __init__(self, message: str, resets_at: Optional[float] = None):
        super().__init__(message)
        self.resets_at = resets_at


@dataclass(frozen=True)
class SubscriptionUsage:
    """Snapshot of subscription window utilization (percentages, 0-100)."""

    five_hour_pct: float
    five_hour_resets_at: Optional[float]  # epoch seconds
    seven_day_pct: float
    seven_day_resets_at: Optional[float]  # epoch seconds
    fetched_at: float


@dataclass
class LimitHit:
    """A limit error observed by the provider, pending/holding a pause."""

    hit_at: float
    resets_at: Optional[float] = None  # parsed from CLI output, if present
    pause_until: Optional[float] = None  # resolved deadline (runner sets it)


def _parse_iso_epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _read_access_token(credentials_path: Optional[Path] = None) -> Optional[str]:
    path = (credentials_path or CREDENTIALS_PATH).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    oauth = data.get("claudeAiOauth", data)
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    return token if isinstance(token, str) and token else None


def _fetch_usage_payload(token: str) -> Optional[dict]:
    request = urllib.request.Request(
        USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _OAUTH_BETA,
            "User-Agent": "shinka-subscription-usage",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("Subscription usage fetch failed: %s", exc)
        return None
    return payload if isinstance(payload, dict) else None


def _usage_from_payload(payload: dict, fetched_at: float) -> Optional[SubscriptionUsage]:
    five_hour = payload.get("five_hour")
    seven_day = payload.get("seven_day")
    if not isinstance(five_hour, dict) or not isinstance(seven_day, dict):
        return None
    try:
        five_hour_pct = float(five_hour.get("utilization") or 0.0)
        seven_day_pct = float(seven_day.get("utilization") or 0.0)
    except (TypeError, ValueError):
        return None
    return SubscriptionUsage(
        five_hour_pct=five_hour_pct,
        five_hour_resets_at=_parse_iso_epoch(five_hour.get("resets_at")),
        seven_day_pct=seven_day_pct,
        seven_day_resets_at=_parse_iso_epoch(seven_day.get("resets_at")),
        fetched_at=fetched_at,
    )


def get_claude_usage(
    cache_ttl: float = 60.0,
    credentials_path: Optional[Path] = None,
) -> Optional[SubscriptionUsage]:
    """Return current subscription window utilization, or None if unavailable.

    Results are cached for ``cache_ttl`` seconds so tight loops do not hammer
    the endpoint. Any failure (no credentials, expired token, network, schema
    change) returns None; callers should treat that as "gating unavailable".
    """
    global _USAGE_CACHE
    now = time.time()
    with _STATE_LOCK:
        cached = _USAGE_CACHE
    if cached is not None and now - cached.fetched_at < cache_ttl:
        return cached

    token = _read_access_token(credentials_path)
    if token is None:
        return None
    payload = _fetch_usage_payload(token)
    if payload is None:
        return None
    usage = _usage_from_payload(payload, fetched_at=now)
    if usage is None:
        logger.debug("Subscription usage payload had unexpected shape.")
        return None
    with _STATE_LOCK:
        _USAGE_CACHE = usage
    return usage


def record_limit_hit(resets_at: Optional[float] = None) -> None:
    """Record that a subscription call failed on an exhausted window.

    Called by the headless provider. Keeps the earliest hit; enriches it with
    a reset time when a later error carries one.
    """
    global _LIMIT_HIT
    with _STATE_LOCK:
        if _LIMIT_HIT is None:
            _LIMIT_HIT = LimitHit(hit_at=time.time(), resets_at=resets_at)
        elif resets_at is not None and _LIMIT_HIT.resets_at is None:
            _LIMIT_HIT.resets_at = resets_at


def get_limit_hit() -> Optional[LimitHit]:
    with _STATE_LOCK:
        return _LIMIT_HIT


def set_limit_pause_until(pause_until: float) -> None:
    """Resolve the recorded hit to a concrete pause deadline (runner-side)."""
    global _LIMIT_HIT
    with _STATE_LOCK:
        if _LIMIT_HIT is None:
            _LIMIT_HIT = LimitHit(hit_at=time.time())
        _LIMIT_HIT.pause_until = pause_until


def clear_limit_hit() -> None:
    global _LIMIT_HIT
    with _STATE_LOCK:
        _LIMIT_HIT = None


def active_limit_pause() -> Optional[float]:
    """Deadline of an in-force pause from a recorded limit hit, else None.

    An unresolved hit (no deadline yet) counts as active so concurrent calls
    fail fast during the window between the hit and the runner's next
    coordinator cycle.
    """
    with _STATE_LOCK:
        hit = _LIMIT_HIT
    if hit is None:
        return None
    if hit.pause_until is None:
        return hit.resets_at or float("inf")
    if time.time() < hit.pause_until:
        return hit.pause_until
    return None


def _reset_state_for_tests() -> None:
    global _USAGE_CACHE, _LIMIT_HIT
    with _STATE_LOCK:
        _USAGE_CACHE = None
        _LIMIT_HIT = None


# Claude subscription runs out of ANTHROPIC_API_KEY scope entirely, so the
# only signals of an exhausted window are the CLI's error strings. Matched
# case-insensitively; deliberately excludes per-minute API "rate limit"
# errors, which resolve in seconds and are handled by normal retries.
_LIMIT_MARKERS = (
    "usage limit reached",
    "5-hour limit reached",
    "session limit reached",
    "weekly limit reached",
    "hit your usage limit",
    "usage limit will reset",
)


def looks_like_limit_error(output: str) -> bool:
    lowered = output.lower()
    return any(marker in lowered for marker in _LIMIT_MARKERS)


def parse_limit_reset_epoch(output: str) -> Optional[float]:
    """Extract a reset epoch from CLI limit errors when present.

    The classic non-interactive format is ``Claude AI usage limit
    reached|<epoch-seconds>``.
    """
    import re

    match = re.search(r"limit reached\|(\d{9,13})", output, re.IGNORECASE)
    if not match:
        return None
    epoch = float(match.group(1))
    if epoch > 1e12:  # milliseconds
        epoch /= 1000.0
    return epoch

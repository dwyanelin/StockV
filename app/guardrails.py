"""Rate limiting, in-flight deduplication, daily quota cap, and tiered response caching.

Designed to prevent group chat button-mashing spam and keep Cloud Run usage
safely within the monthly free tier.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_TW_TZ = timezone(timedelta(hours=8))

# Tiered cache TTLs (seconds) by command kind:
# - Live quote & intraday: 10s (keeps live prices fresh while deduplicating bursts)
# - Candlesticks: 30s (saves mplfinance CPU while keeping latest candle accurate)
# - Post-market / slow-moving data: 600s (10 min; zero freshness downside)
CACHE_TTL_BY_KIND: dict[str, float] = {
    "#": 10.0,
    "P": 10.0,
    "K": 30.0,
    "KW": 30.0,
    "KM": 30.0,
    "T": 600.0,
    "D": 600.0,
    "F": 600.0,
}


@dataclass(frozen=True)
class AcquireResult:
    allowed: bool
    reason: str = ""  # "", "user_cooldown", "group_cooldown", "daily_limit"


class RateLimiter:
    """Thread-safe atomic rate limiter covering:
    1. Per-user cooldown (default 3s) — atomic check-and-set also guarantees in-flight dedup.
    2. Per-group same (kind, query) cooldown (default 30s).
    3. Daily global hard cap (default 1,500 requests/day, Taiwan time).
    """

    def __init__(
        self,
        user_cooldown: float = 3.0,
        group_cmd_cooldown: float = 30.0,
        daily_limit: int = 1500,
    ) -> None:
        self.user_cooldown = user_cooldown
        self.group_cmd_cooldown = group_cmd_cooldown
        self.daily_limit = daily_limit
        self._lock = threading.Lock()
        self._user_ts: dict[str, float] = {}
        self._group_ts: dict[tuple[str, str, str], float] = {}
        self._day_key: str = self._today_key()
        self._day_count: int = 0

    @staticmethod
    def _today_key() -> str:
        return datetime.now(_TW_TZ).strftime("%Y-%m-%d")

    def _prune(self, now: float) -> None:
        if len(self._user_ts) > 500:
            cutoff = now - max(self.user_cooldown * 4, 60.0)
            self._user_ts = {k: v for k, v in self._user_ts.items() if v > cutoff}
        if len(self._group_ts) > 1000:
            cutoff = now - max(self.group_cmd_cooldown * 4, 300.0)
            self._group_ts = {k: v for k, v in self._group_ts.items() if v > cutoff}

    def try_acquire(
        self,
        user_id: str | None,
        group_id: str | None,
        kind: str,
        target_key: str,
        now: float | None = None,
    ) -> AcquireResult:
        """Atomically check and reserve cooldown + daily quota slots."""
        t = time.monotonic() if now is None else now
        kind_u = kind.upper()
        target_u = target_key.strip().upper()
        today = self._today_key()

        with self._lock:
            if today != self._day_key:
                self._day_key = today
                self._day_count = 0

            if self.daily_limit > 0 and self._day_count >= self.daily_limit:
                return AcquireResult(False, "daily_limit")

            if user_id and self.user_cooldown > 0:
                last_u = self._user_ts.get(user_id)
                if last_u is not None and (t - last_u) < self.user_cooldown:
                    return AcquireResult(False, "user_cooldown")

            gkey = (group_id, kind_u, target_u) if group_id else None
            if gkey is not None and self.group_cmd_cooldown > 0:
                last_g = self._group_ts.get(gkey)
                if last_g is not None and (t - last_g) < self.group_cmd_cooldown:
                    return AcquireResult(False, "group_cooldown")

            # Reserve atomically before any I/O so concurrent clicks are deduplicated
            if user_id:
                self._user_ts[user_id] = t
            if gkey is not None:
                self._group_ts[gkey] = t
            self._day_count += 1
            self._prune(t)
            return AcquireResult(True)

    def release_group_slot(self, group_id: str | None, kind: str, target_key: str) -> None:
        """Release a group cooldown slot when a command fails due to a transient error."""
        if not group_id:
            return
        gkey = (group_id, kind.upper(), target_key.strip().upper())
        with self._lock:
            self._group_ts.pop(gkey, None)

    def reset(self) -> None:
        """Clear all limiter state (used by tests)."""
        with self._lock:
            self._user_ts.clear()
            self._group_ts.clear()
            self._day_key = self._today_key()
            self._day_count = 0


class ResponseCache:
    """Thread-safe in-memory TTL cache for built LINE reply messages."""

    def __init__(self, max_entries: int = 200) -> None:
        self.max_entries = max_entries
        self._lock = threading.Lock()
        # (kind, target_key) -> (expires_at, messages)
        self._store: dict[tuple[str, str], tuple[float, list]] = {}

    @staticmethod
    def ttl_for(kind: str) -> float:
        return CACHE_TTL_BY_KIND.get(kind.upper(), 10.0)

    def get(self, kind: str, target_key: str, now: float | None = None) -> list | None:
        t = time.monotonic() if now is None else now
        key = (kind.upper(), target_key.strip().upper())
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, messages = item
            if t >= expires_at:
                self._store.pop(key, None)
                return None
            return messages

    def set(self, kind: str, target_key: str, messages: list, now: float | None = None) -> None:
        ttl = self.ttl_for(kind)
        if ttl <= 0:
            return
        t = time.monotonic() if now is None else now
        key = (kind.upper(), target_key.strip().upper())
        with self._lock:
            self._store[key] = (t + ttl, messages)
            if len(self._store) > self.max_entries:
                # Evict expired entries first, then oldest
                expired = [k for k, (exp, _) in self._store.items() if t >= exp]
                for k in expired:
                    self._store.pop(k, None)
                while len(self._store) > self.max_entries:
                    oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
                    self._store.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

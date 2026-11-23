"""Taiwan stock directory (TWSE listed + TPEx OTC + Emerging/ESB).

Maps Chinese stock names (e.g. '玉晶光', '倍利科', '台積電', '世芯') and stock
codes (including alphanumeric ETFs like '00631L') to their ticker and Yahoo
Finance suffix ('.TW' or '.TWO').
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (StockV LINE bot)"}
_CACHE_TTL_SECONDS = 12 * 3600

_SOURCES = (
    ("TWSE", ".TW", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name"),
    ("TPEX", ".TWO", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", "SecuritiesCompanyCode", "CompanyName"),
    ("ESB", ".TWO", "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics", "SecuritiesCompanyCode", "CompanyName"),
)

_ALIAS_SUFFIX_RE = re.compile(r"(?:-KY|\*)+$", re.IGNORECASE)


@dataclass(frozen=True)
class TwStock:
    code: str
    name: str
    yahoo_suffix: str  # ".TW" or ".TWO"
    market: str        # "TWSE", "TPEX", or "ESB"

    @property
    def yahoo_symbol(self) -> str:
        return f"{self.code}{self.yahoo_suffix}"


@dataclass
class _DirectoryIndex:
    by_code: dict[str, TwStock]
    by_name: dict[str, TwStock]
    by_alias: dict[str, TwStock]
    loaded_at: float


_lock = threading.Lock()
_cache: _DirectoryIndex | None = None


def _normalize(text: str) -> str:
    """Normalize stock name for forgiving lookup (台/臺, fullwidth symbols, case)."""
    s = text.strip().upper()
    s = s.replace("臺", "台")
    s = s.replace("－", "-").replace("＊", "*").replace("＋", "+")
    return "".join(s.split())


def _fetch_source(
    market: str, suffix: str, url: str, code_key: str, name_key: str
) -> list[TwStock]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list):
            return []
        stocks: list[TwStock] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get(code_key, "")).strip().upper()
            name = str(row.get(name_key, "")).strip()
            if code and name:
                stocks.append(TwStock(code=code, name=name, yahoo_suffix=suffix, market=market))
        return stocks
    except Exception:
        log.warning("Failed to fetch %s stock directory from %s", market, url, exc_info=True)
        return []


def _build_index(stocks: list[TwStock]) -> _DirectoryIndex:
    by_code: dict[str, TwStock] = {}
    by_name: dict[str, TwStock] = {}
    alias_candidates: dict[str, list[TwStock]] = {}

    for stock in stocks:
        by_code.setdefault(stock.code, stock)
        norm = _normalize(stock.name)
        by_name.setdefault(norm, stock)

        stripped = _ALIAS_SUFFIX_RE.sub("", norm)
        if stripped and stripped != norm:
            alias_candidates.setdefault(stripped, []).append(stock)

    # Only register stripped aliases (e.g. '世芯' -> '世芯-KY', '康霈' -> '康霈*')
    # when unambiguous and not colliding with an exact stock name.
    by_alias: dict[str, TwStock] = {}
    for alias, matches in alias_candidates.items():
        if alias not in by_name and len(matches) == 1:
            by_alias[alias] = matches[0]

    return _DirectoryIndex(
        by_code=by_code,
        by_name=by_name,
        by_alias=by_alias,
        loaded_at=time.monotonic(),
    )


def _get_index() -> _DirectoryIndex:
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache.loaded_at) < _CACHE_TTL_SECONDS:
        return _cache

    with _lock:
        now = time.monotonic()
        if _cache is not None and (now - _cache.loaded_at) < _CACHE_TTL_SECONDS:
            return _cache

        with ThreadPoolExecutor(max_workers=len(_SOURCES)) as pool:
            futures = [pool.submit(_fetch_source, *src) for src in _SOURCES]
            stocks: list[TwStock] = []
            for fut in futures:
                stocks.extend(fut.result())

        if stocks:
            _cache = _build_index(stocks)
            log.info(
                "Loaded Taiwan stock directory: %d codes, %d names, %d aliases",
                len(_cache.by_code),
                len(_cache.by_name),
                len(_cache.by_alias),
            )
            return _cache

        if _cache is not None:
            return _cache
        return _DirectoryIndex(by_code={}, by_name={}, by_alias={}, loaded_at=0.0)


def lookup_code(code: str) -> TwStock | None:
    """Find a Taiwan stock by its code (e.g. '2330', '3406', '00631L')."""
    key = code.strip().upper()
    if not key:
        return None
    return _get_index().by_code.get(key)


def lookup_name(name: str) -> TwStock | None:
    """Find a Taiwan stock by its Chinese/official short name (e.g. '玉晶光', '倍利科')."""
    key = _normalize(name)
    if not key:
        return None
    idx = _get_index()
    return idx.by_name.get(key) or idx.by_alias.get(key)


def lookup(query: str) -> TwStock | None:
    """Find a Taiwan stock by either code or name."""
    return lookup_code(query) or lookup_name(query)


def clear_cache() -> None:
    """Clear the in-memory directory cache (used by tests)."""
    global _cache
    with _lock:
        _cache = None

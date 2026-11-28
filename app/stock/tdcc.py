"""TDCC weekly equity dispersion snapshot parser and runtime loader.

- Runtime: reads a compact pre-aggregated JSON snapshot from Cloud Storage
  (cached 6 hours in memory, 4s timeout, graceful None fallback) so LINE
  replies never wait on TDCC's multi-megabyte CSV download.
- Sync script (weekly GitHub Actions cron): downloads TDCC's official open data
  CSV, parses 1,000+ lot & 400+ lot ratios and total shareholders, and merges
  with the previous week's snapshot to compute week-over-week deltas.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from dataclasses import dataclass

import requests

from app import config

log = logging.getLogger(__name__)

TDCC_CSV_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
_HEADERS = {"User-Agent": "Mozilla/5.0 (StockV LINE bot)"}
_CACHE_TTL = 6 * 3600.0
_FAIL_RETRY_TTL = 300.0

_WHALE_400_LEVELS = {"12", "13", "14", "15"}
_WHALE_1000_LEVEL = "15"
_TOTAL_LEVEL = "17"


@dataclass(frozen=True)
class TdccSummary:
    date_str: str                      # e.g. "2026/11/27"
    whale_1000_pct: float              # >= 1,000 lots (%)
    whale_400_pct: float               # >= 400 lots (%)
    shareholders: int                  # total shareholder count
    whale_1000_chg: float | None = None
    whale_400_chg: float | None = None
    shareholders_chg: int | None = None


@dataclass
class _TdccCache:
    by_code: dict[str, TdccSummary]
    loaded_at: float
    ttl: float


_lock = threading.Lock()
_cache: _TdccCache | None = None


def _fmt_ymd(raw: str) -> str:
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}/{s[4:6]}/{s[6:8]}"
    return s


def _to_float(val: str) -> float:
    try:
        return float(str(val).replace(",", "").strip() or 0.0)
    except ValueError:
        return 0.0


def _to_int(val: str) -> int:
    try:
        return int(float(str(val).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def parse_tdcc_csv(csv_text: str) -> tuple[str, dict[str, tuple[float, float, int]]]:
    """Parse TDCC open data CSV into (YYYYMMDD, {code: (pct_1000, pct_400, holders)})."""
    reader = csv.reader(io.StringIO(csv_text.lstrip("\ufeff")))
    next(reader, None)  # skip header

    latest_date = ""
    acc: dict[str, list[float]] = {}  # code -> [p1000, p400, holders]
    for row in reader:
        if len(row) < 6:
            continue
        ymd = row[0].strip()
        code = row[1].strip().upper()
        level = row[2].strip()
        if not code:
            continue
        if ymd and ymd > latest_date:
            latest_date = ymd

        bucket = acc.get(code)
        if bucket is None:
            bucket = [0.0, 0.0, 0.0]
            acc[code] = bucket

        if level == _WHALE_1000_LEVEL:
            pct = _to_float(row[5])
            bucket[0] = pct
            bucket[1] += pct
        elif level in _WHALE_400_LEVELS:
            bucket[1] += _to_float(row[5])
        elif level == _TOTAL_LEVEL:
            bucket[2] = float(_to_int(row[3]))

    stocks: dict[str, tuple[float, float, int]] = {}
    for code, (p1000, p400, holders_f) in acc.items():
        holders = int(holders_f)
        if holders <= 0:
            continue
        stocks[code] = (round(p1000, 2), round(min(p400, 100.0), 2), holders)
    return latest_date, stocks


def merge_snapshot(
    new_date: str,
    new_stocks: dict[str, tuple[float, float, int]],
    existing: dict | None = None,
) -> dict:
    """Build compact snapshot dict, preserving previous week's numbers for WoW deltas."""
    existing_date = str((existing or {}).get("date") or "")
    existing_stocks = (existing or {}).get("stocks") or {}

    if existing_date and existing_date != new_date:
        # New week arrived -> shift previous current values into prev slots
        prev_date = existing_date
        prev_source = {
            k: (v[0], v[1], v[2])
            for k, v in existing_stocks.items()
            if isinstance(v, list) and len(v) >= 3
        }
    else:
        # Same week re-run (idempotent) or first ever snapshot
        prev_date = str((existing or {}).get("prev_date") or "")
        prev_source = {
            k: (v[3], v[4], v[5])
            for k, v in existing_stocks.items()
            if isinstance(v, list) and len(v) >= 6
        }

    merged: dict[str, list] = {}
    for code, (p1000, p400, holders) in new_stocks.items():
        prev = prev_source.get(code)
        if prev is not None:
            merged[code] = [p1000, p400, holders, prev[0], prev[1], prev[2]]
        else:
            merged[code] = [p1000, p400, holders]

    return {
        "date": new_date,
        "prev_date": prev_date,
        "stocks": merged,
    }


def _parse_snapshot_json(payload: dict) -> dict[str, TdccSummary]:
    date_str = _fmt_ymd(str(payload.get("date") or ""))
    raw_stocks = payload.get("stocks")
    if not date_str or not isinstance(raw_stocks, dict):
        return {}

    result: dict[str, TdccSummary] = {}
    for code, vals in raw_stocks.items():
        if not isinstance(vals, list) or len(vals) < 3:
            continue
        p1000, p400, holders = float(vals[0]), float(vals[1]), int(vals[2])
        if len(vals) >= 6:
            d1000 = round(p1000 - float(vals[3]), 2)
            d400 = round(p400 - float(vals[4]), 2)
            d_holders = holders - int(vals[5])
        else:
            d1000 = d400 = d_holders = None
        result[str(code).strip().upper()] = TdccSummary(
            date_str=date_str,
            whale_1000_pct=p1000,
            whale_400_pct=p400,
            shareholders=holders,
            whale_1000_chg=d1000,
            whale_400_chg=d400,
            shareholders_chg=d_holders,
        )
    return result


def _load_cache() -> dict[str, TdccSummary]:
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache.loaded_at) < _cache.ttl:
        return _cache.by_code

    with _lock:
        now = time.monotonic()
        if _cache is not None and (now - _cache.loaded_at) < _cache.ttl:
            return _cache.by_code

        url = config.TDCC_SNAPSHOT_URL
        if not url:
            return {}
        try:
            r = requests.get(url, headers=_HEADERS, timeout=(3, 4))
            r.raise_for_status()
            parsed = _parse_snapshot_json(r.json())
            if parsed:
                _cache = _TdccCache(parsed, time.monotonic(), _CACHE_TTL)
                log.info("Loaded TDCC snapshot: %d stocks", len(parsed))
                return parsed
        except Exception:
            log.warning("Failed to load TDCC snapshot from %s", url, exc_info=True)

        if _cache is not None:
            return _cache.by_code
        _cache = _TdccCache({}, time.monotonic(), _FAIL_RETRY_TTL)
        return {}


def lookup(code: str) -> TdccSummary | None:
    """Lookup weekly TDCC equity dispersion summary for a Taiwan stock code."""
    key = code.strip().upper()
    if not key:
        return None
    return _load_cache().get(key)


def clear_cache() -> None:
    """Clear the in-memory TDCC snapshot cache (used by tests)."""
    global _cache
    with _lock:
        _cache = None

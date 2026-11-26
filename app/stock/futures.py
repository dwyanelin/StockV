"""Taiwan Futures Exchange (TAIFEX) real-time quotes and 1-minute intraday charts.

Supports front-month Taiwan Index Futures (TXF 台指期) and Mini-TAIEX (MXF 小台)
with automatic day (日盤) / after-hours (夜盤) session selection.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

_TW_TZ = timezone(timedelta(hours=8))
_API_BASE = "https://mis.taifex.com.tw/futures/api"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (StockV LINE bot)",
    "Content-Type": "application/json",
}
_TIMEOUT = (4, 6)


class FuturesDataUnavailable(Exception):
    """Raised when TAIFEX futures data cannot be retrieved."""


def _fnum(val) -> float | None:
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if not s or s in ("-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _preferred_sessions(now: datetime | None = None) -> tuple[tuple[str, str], ...]:
    """Return ((market_type, label), ...) ordered by active TAIFEX session."""
    dt = datetime.now(_TW_TZ) if now is None else now
    hm = dt.hour * 100 + dt.minute
    if 845 <= hm < 1500:
        return (("0", "日盤"), ("1", "夜盤"))
    return (("1", "夜盤"), ("0", "日盤"))


def _post(endpoint: str, payload: dict) -> dict | None:
    try:
        r = requests.post(f"{_API_BASE}/{endpoint}", json=payload, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception:
        log.warning("TAIFEX request failed: %s", endpoint, exc_info=True)
        return None


def _pick_front_contract(cid: str, now: datetime | None = None) -> tuple[dict, str]:
    """Fetch the active front-month futures contract row and session label."""
    for mtype, label in _preferred_sessions(now):
        j = _post(
            "getQuoteList",
            {
                "MarketType": mtype,
                "SymbolType": "F",
                "KindID": "1",
                "CID": cid.upper(),
                "ExpireMonth": "",
                "RowSize": "全部",
                "PageNo": "",
                "SortColumn": "",
                "AscDesc": "A",
            },
        )
        qlist = (j or {}).get("RtData", {}).get("QuoteList") or []
        for row in qlist:
            sym_id = str(row.get("SymbolID", "")).strip()
            cname = str(row.get("DispCName", "")).strip()
            if not sym_id or sym_id.endswith("-S") or "現貨" in cname:
                continue
            if _fnum(row.get("CLastPrice")) is not None:
                return row, label
    raise FuturesDataUnavailable(cid)


def fetch_quote(cid: str, now: datetime | None = None) -> dict:
    """Return normalized quote dict for a TAIFEX futures product (e.g. 'TXF', 'MXF')."""
    row, session = _pick_front_contract(cid, now=now)
    price = _fnum(row.get("CLastPrice"))
    if price is None:
        raise FuturesDataUnavailable(cid)

    ctime = str(row.get("CTime", "")).strip()
    dt_now = datetime.now(_TW_TZ) if now is None else now
    if len(ctime) == 6 and ctime.isdigit():
        as_of = f"{dt_now:%Y/%m/%d} {ctime[:2]}:{ctime[2:4]}"
    else:
        as_of = dt_now.strftime("%Y/%m/%d %H:%M")

    cname = str(row.get("DispCName", cid)).strip()
    return {
        "symbol_id": str(row.get("SymbolID", "")).strip(),
        "name": f"{cname} ({session})",
        "session": session,
        "price": price,
        "prev_close": _fnum(row.get("CRefPrice")),
        "open": _fnum(row.get("COpenPrice")),
        "high": _fnum(row.get("CHighPrice")),
        "low": _fnum(row.get("CLowPrice")),
        "volume": _fnum(row.get("CTotalVolume")) or 0.0,
        "as_of": as_of,
    }


def fetch_intraday(cid: str, now: datetime | None = None) -> tuple[dict, pd.DataFrame, float | None]:
    """Fetch quote metadata + 1-minute OHLCV DataFrame for a TAIFEX futures contract."""
    q = fetch_quote(cid, now=now)
    j = _post("getChartData1M", {"SymbolID": q["symbol_id"]})
    ticks = (j or {}).get("RtData", {}).get("Ticks") or []
    if not ticks:
        raise FuturesDataUnavailable(cid)

    dt_now = datetime.now(_TW_TZ) if now is None else now
    base_date = dt_now.date()
    is_night = q["session"] == "夜盤"
    # Night session starts at 15:00 and crosses midnight until 05:00
    if is_night and dt_now.hour < 6:
        start_date = base_date - timedelta(days=1)
    else:
        start_date = base_date

    index: list[pd.Timestamp] = []
    rows: list[tuple[float, float, float, float, float]] = []
    for tick in ticks:
        if not isinstance(tick, list) or len(tick) < 6:
            continue
        tstr = str(tick[0]).strip().zfill(6)
        if not tstr.isdigit():
            continue
        hh, mm, ss = int(tstr[:2]), int(tstr[2:4]), int(tstr[4:6])
        o, h, lo, c = _fnum(tick[1]), _fnum(tick[2]), _fnum(tick[3]), _fnum(tick[4])
        v = _fnum(tick[5]) or 0.0
        if None in (o, h, lo, c):
            continue
        d = start_date + timedelta(days=1) if (is_night and hh < 12) else start_date
        ts = pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=_TW_TZ))
        index.append(ts)
        rows.append((o, h, lo, c, v))

    if not rows:
        raise FuturesDataUnavailable(cid)

    df = pd.DataFrame(rows, index=pd.DatetimeIndex(index), columns=["Open", "High", "Low", "Close", "Volume"])
    return q, df, q["prev_close"]

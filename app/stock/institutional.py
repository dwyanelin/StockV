"""Institutional investor data from TWSE (listed + market) and TPEx (OTC).

- TWSE T86: daily net buy/sell shares for listed stocks.
- TWSE MI_QFIIS: foreign & mainland investor shareholding ratio (listed).
- TWSE BFI82U: market-wide institutional net buy/sell amount (in NTD -> 億元).
- TPEx 3itrade_hedge_result: daily net buy/sell shares for OTC stocks.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests

from app.stock import tdcc

log = logging.getLogger(__name__)

_TWSE = "https://www.twse.com.tw/rwd/zh/fund"
_TPEX_INST = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
_HEADERS = {"User-Agent": "Mozilla/5.0 (StockV LINE bot)"}

# date -> {ticker: (name, foreign, trust, dealer, total)} ; None = holiday/no data
_t86_cache: dict[date, dict[str, tuple] | None] = {}
_tpex_cache: dict[date, dict[str, tuple] | None] = {}
# date -> (foreign, trust, dealer, total) in NTD ; None = holiday/no data
_bfi82u_cache: dict[date, tuple[float, float, float, float] | None] = {}


class InstitutionalDataUnavailable(Exception):
    """No institutional data for this ticker (unlisted / emerging stock)."""


@dataclass
class InstDay:
    day: date
    foreign: float      # net shares (stocks) or net NTD (market)
    trust: float
    dealer: float
    total: float


@dataclass
class InstReport:
    ticker: str
    name: str
    days: list[InstDay] = field(default_factory=list)
    foreign_holding_pct: float | None = None
    foreign_holding_date: date | None = None
    unit: str = "張"       # "張" for stocks, "億元" for market
    scale: float = 1000.0  # 1000 shares/lot for stocks, 1e8 NTD/億元 for market
    market: str = "TWSE"   # "TWSE", "TPEX", or "MARKET"
    tdcc: tdcc.TdccSummary | None = None


def _get_json(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        j = r.json()
        stat = str(j.get("stat", "")).upper()
        if stat == "OK" and (j.get("data") or j.get("tables")):
            return j
        return None
    except Exception:
        log.warning("Institutional request failed: %s", url, exc_info=True)
        return None


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _trim_cache(cache: dict, limit: int = 40) -> None:
    if len(cache) > limit:
        for k in sorted(cache)[: len(cache) - limit]:
            del cache[k]


def _t86_table(day: date) -> dict[str, tuple] | None:
    """Whole-market TWSE T86 table for one day, cached. None on holidays."""
    if day in _t86_cache:
        return _t86_cache[day]

    url = f"{_TWSE}/T86?date={day:%Y%m%d}&selectType=ALLBUT0999&response=json"
    j = _get_json(url)
    table = None
    if j and j.get("data"):
        fields = j["fields"]
        idx = {name: i for i, name in enumerate(fields)}

        def col(*names):
            return next((idx[n] for n in names if n in idx), None)

        c_id, c_name = col("證券代號"), col("證券名稱")
        c_foreign = col("外陸資買賣超股數(不含外資自營商)", "外資買賣超股數")
        c_trust, c_dealer = col("投信買賣超股數"), col("自營商買賣超股數")
        c_total = col("三大法人買賣超股數")
        if c_id is not None:
            table = {}
            for row in j["data"]:
                table[str(row[c_id]).strip()] = (
                    str(row[c_name]).strip() if c_name is not None else "",
                    _num(row[c_foreign]) if c_foreign is not None else 0,
                    _num(row[c_trust]) if c_trust is not None else 0,
                    _num(row[c_dealer]) if c_dealer is not None else 0,
                    _num(row[c_total]) if c_total is not None else 0,
                )

    if table is not None or day < date.today():
        _t86_cache[day] = table
    _trim_cache(_t86_cache)
    return table


def _tpex_table(day: date) -> dict[str, tuple] | None:
    """Whole-market TPEx OTC institutional table for one day, cached."""
    if day in _tpex_cache:
        return _tpex_cache[day]

    roc_date = f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"
    url = f"{_TPEX_INST}?l=zh-tw&o=json&se=EW&t=D&d={roc_date}"
    j = _get_json(url)
    table = None
    if j and j.get("tables"):
        rows = j["tables"][0].get("data") or []
        if rows:
            table = {}
            for row in rows:
                if len(row) < 24:
                    continue
                code = str(row[0]).strip()
                name = str(row[1]).strip()
                # Verified indices: [10]=外資合計, [13]=投信, [22]=自營商合計, [23]=三大法人合計
                foreign = _num(row[10])
                trust = _num(row[13])
                dealer = _num(row[22])
                total = _num(row[23])
                table[code] = (name, foreign, trust, dealer, total)

    if table is not None or day < date.today():
        _tpex_cache[day] = table
    _trim_cache(_tpex_cache)
    return table


def _bfi82u_day(day: date) -> tuple[float, float, float, float] | None:
    """TWSE market-wide institutional net buy/sell amount (in NTD) for one day."""
    if day in _bfi82u_cache:
        return _bfi82u_cache[day]

    url = f"{_TWSE}/BFI82U?dayDate={day:%Y%m%d}&type=day&response=json"
    j = _get_json(url)
    entry = None
    if j and j.get("data"):
        foreign = trust = dealer = total = 0.0
        for row in j["data"]:
            if len(row) < 4:
                continue
            label = str(row[0]).strip()
            net = _num(row[3])
            if "外資" in label:
                foreign += net
            elif "投信" in label:
                trust += net
            elif "自營商" in label:
                dealer += net
            elif "合計" in label:
                total = net
        if total == 0.0 and (foreign or trust or dealer):
            total = foreign + trust + dealer
        entry = (foreign, trust, dealer, total)

    if entry is not None or day < date.today():
        _bfi82u_cache[day] = entry
    _trim_cache(_bfi82u_cache)
    return entry


def _foreign_holding(ticker: str) -> tuple[float, date] | None:
    """Latest foreign shareholding ratio (%), walking back over holidays (TWSE listed)."""
    day = date.today()
    for _ in range(7):
        if day.weekday() < 5:
            url = f"{_TWSE}/MI_QFIIS?date={day:%Y%m%d}&selectType=ALLBUT0999&response=json"
            j = _get_json(url)
            if j and j.get("data"):
                fields = j["fields"]
                idx = {name: i for i, name in enumerate(fields)}
                c_id = idx.get("證券代號")
                c_pct = next((idx[n] for n in fields if "持股比率" in n), None)
                if c_id is None or c_pct is None:
                    return None
                for row in j["data"]:
                    if str(row[c_id]).strip() == ticker:
                        return _num(row[c_pct]), day
                return None
        day -= timedelta(days=1)
    return None


def _recent_weekdays(count: int) -> list[date]:
    weekdays: list[date] = []
    day = date.today()
    while len(weekdays) < count:
        if day.weekday() < 5:
            weekdays.append(day)
        day -= timedelta(days=1)
    return weekdays


def fetch_market(num_days: int = 10) -> InstReport:
    """Recent TWSE market-wide institutional net buy/sell (in 億元)."""
    weekdays = _recent_weekdays(num_days + 6)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_bfi82u_day, weekdays))

    report = InstReport(
        ticker="台股",
        name="台股大盤",
        unit="億元",
        scale=1e8,
        market="MARKET",
    )
    for d, entry in zip(weekdays, results, strict=False):
        if entry is not None:
            foreign, trust, dealer, total = entry
            report.days.append(InstDay(d, foreign, trust, dealer, total))

    if not report.days:
        raise InstitutionalDataUnavailable("台股")

    report.days.sort(key=lambda x: x.day)
    report.days = report.days[-num_days:]
    return report


def fetch(ticker: str, num_days: int = 10, market: str | None = None) -> InstReport:
    """Recent institutional net buy/sell for a TWSE-listed or TPEx-OTC ticker."""
    weekdays = _recent_weekdays(num_days + 6)

    # If explicitly known to be TPEx OTC, query TPEx directly
    if market == "TPEX":
        return _fetch_tpex(ticker, weekdays, num_days)

    with ThreadPoolExecutor(max_workers=6) as pool:
        holding_future = pool.submit(_foreign_holding, ticker)
        tdcc_future = pool.submit(tdcc.lookup, ticker)
        tables = list(pool.map(_t86_table, weekdays))

    report = InstReport(ticker=ticker, name=ticker, market="TWSE")
    for d, table in zip(weekdays, tables, strict=False):
        if table and ticker in table:
            name, foreign, trust, dealer, total = table[ticker]
            report.days.append(InstDay(d, foreign, trust, dealer, total))
            if name:
                report.name = name

    if report.days:
        report.days.sort(key=lambda x: x.day)
        report.days = report.days[-num_days:]
        holding = holding_future.result()
        if holding:
            report.foreign_holding_pct, report.foreign_holding_date = holding
        report.tdcc = tdcc_future.result()
        return report

    # Fallback to TPEx OTC if not found on TWSE
    return _fetch_tpex(ticker, weekdays, num_days, tdcc_summary=tdcc_future.result())


def _fetch_tpex(
    ticker: str,
    weekdays: list[date],
    num_days: int,
    tdcc_summary: tdcc.TdccSummary | None = None,
) -> InstReport:
    with ThreadPoolExecutor(max_workers=6) as pool:
        tdcc_future = pool.submit(tdcc.lookup, ticker) if tdcc_summary is None else None
        tables = list(pool.map(_tpex_table, weekdays))

    report = InstReport(ticker=ticker, name=ticker, market="TPEX")
    for d, table in zip(weekdays, tables, strict=False):
        if table and ticker in table:
            name, foreign, trust, dealer, total = table[ticker]
            report.days.append(InstDay(d, foreign, trust, dealer, total))
            if name:
                report.name = name

    if not report.days:
        raise InstitutionalDataUnavailable(ticker)

    report.days.sort(key=lambda x: x.day)
    report.days = report.days[-num_days:]
    report.tdcc = tdcc_summary if tdcc_future is None else tdcc_future.result()
    return report

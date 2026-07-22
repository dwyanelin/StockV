"""Institutional investor data from TWSE (Taiwan listed stocks).

- T86: daily net buy/sell by the three institutional investor groups
  (foreign investors, investment trusts, dealers).
- MI_QFIIS: foreign & mainland investor shareholding ratio.

Full-day tables are cached in memory, and recent days are fetched in
parallel, so a T command answers in a few seconds even on a cold start.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

_TWSE = "https://www.twse.com.tw/rwd/zh/fund"
_HEADERS = {"User-Agent": "Mozilla/5.0 (StockV LINE bot)"}

# date -> {ticker: (name, foreign, trust, dealer, total)} ; None = holiday/no data
_t86_cache: dict[date, dict[str, tuple] | None] = {}


class InstitutionalDataUnavailable(Exception):
    """No TWSE institutional data for this ticker (unlisted / OTC stock)."""


@dataclass
class InstDay:
    day: date
    foreign: float      # net shares, foreign investors (excl. foreign dealers)
    trust: float        # investment trusts
    dealer: float       # dealer proprietary trading
    total: float


@dataclass
class InstReport:
    ticker: str
    name: str
    days: list[InstDay] = field(default_factory=list)
    foreign_holding_pct: float | None = None
    foreign_holding_date: date | None = None


def _get_json(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        j = r.json()
        return j if j.get("stat") == "OK" and j.get("data") else None
    except Exception:
        log.warning("TWSE request failed: %s", url, exc_info=True)
        return None


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _t86_table(day: date) -> dict[str, tuple] | None:
    """Whole-market T86 table for one day, cached. None on holidays."""
    if day in _t86_cache:
        return _t86_cache[day]

    url = f"{_TWSE}/T86?date={day:%Y%m%d}&selectType=ALLBUT0999&response=json"
    j = _get_json(url)
    table = None
    if j:
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

    # cache holidays only for past days; "today before data release" may fill in later
    if table is not None or day < date.today():
        _t86_cache[day] = table
    if len(_t86_cache) > 40:
        for k in sorted(_t86_cache)[: len(_t86_cache) - 40]:
            del _t86_cache[k]
    return table


def _foreign_holding(ticker: str) -> tuple[float, date] | None:
    """Latest foreign shareholding ratio (%), walking back over holidays."""
    day = date.today()
    for _ in range(7):
        if day.weekday() < 5:
            url = f"{_TWSE}/MI_QFIIS?date={day:%Y%m%d}&selectType=ALLBUT0999&response=json"
            j = _get_json(url)
            if j:
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


def fetch(ticker: str, num_days: int = 10) -> InstReport:
    """Recent institutional net buy/sell + foreign holding ratio for a TWSE-listed ticker."""
    weekdays = []
    day = date.today()
    while len(weekdays) < num_days + 6:  # extra to cover holidays
        if day.weekday() < 5:
            weekdays.append(day)
        day -= timedelta(days=1)

    with ThreadPoolExecutor(max_workers=6) as pool:
        holding_future = pool.submit(_foreign_holding, ticker)
        tables = list(pool.map(_t86_table, weekdays))

    report = InstReport(ticker=ticker, name=ticker)
    for d, table in zip(weekdays, tables):
        if table and ticker in table:
            name, foreign, trust, dealer, total = table[ticker]
            report.days.append(InstDay(d, foreign, trust, dealer, total))
            if name:
                report.name = name

    if not report.days:
        raise InstitutionalDataUnavailable(ticker)

    report.days.sort(key=lambda x: x.day)
    report.days = report.days[-num_days:]

    holding = holding_future.result()
    if holding:
        report.foreign_holding_pct, report.foreign_holding_date = holding
    return report

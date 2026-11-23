"""Fundamentals, valuation, monthly revenue, and margin trading data.

- Taiwan stocks (TWSE + TPEx): cached OpenAPI snapshots for PE/PB/Yield,
  monthly revenue (MoM / YoY / cumulative YoY), and margin/short balances.
- US stocks: Yahoo Finance .info fundamentals snapshot.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests
import yfinance as yf

from app.stock.data import StockQuote

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (StockV LINE bot)"}
_CACHE_TTL = 6 * 3600


class FundamentalDataUnavailable(Exception):
    """Raised when fundamental data cannot be retrieved for a ticker."""


@dataclass
class Valuation:
    pe: float | None = None
    pb: float | None = None
    yield_pct: float | None = None


@dataclass
class MonthlyRevenue:
    period: str               # e.g. "2026/10"
    revenue_yi: float | None  # 億元
    mom_pct: float | None
    yoy_pct: float | None
    cum_yoy_pct: float | None


@dataclass
class MarginInfo:
    margin_balance: float | None = None   # 融資餘額 (張)
    margin_change: float | None = None    # 融資增減 (張)
    short_balance: float | None = None    # 融券餘額 (張)
    short_change: float | None = None     # 融券增減 (張)
    short_ratio_pct: float | None = None  # 券資比 (%)


@dataclass
class FundamentalReport:
    quote: StockQuote
    valuation: Valuation | None = None
    revenue: MonthlyRevenue | None = None
    margin: MarginInfo | None = None
    # US-specific extras
    market_cap: float | None = None
    forward_pe: float | None = None
    rev_growth_pct: float | None = None
    profit_margin_pct: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None


@dataclass
class _TwFundCache:
    valuation: dict[str, Valuation]
    revenue: dict[str, MonthlyRevenue]
    margin: dict[str, MarginInfo]
    loaded_at: float


_lock = threading.Lock()
_cache: _TwFundCache | None = None


def _fnum(s) -> float | None:
    if s is None:
        return None
    txt = str(s).replace(",", "").strip()
    if not txt or txt in ("-", "--", "N/A", "NaN"):
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _roc_ym(raw: str) -> str:
    s = str(raw).strip()
    if len(s) == 5 and s.isdigit():
        return f"{int(s[:3]) + 1911}/{s[3:]}"
    return s


def _fetch_list(url: str) -> list[dict]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        j = r.json()
        return j if isinstance(j, list) else []
    except Exception:
        log.warning("Fundamentals fetch failed: %s", url, exc_info=True)
        return []


def _load_tw_cache() -> _TwFundCache:
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache.loaded_at) < _CACHE_TTL:
        return _cache

    with _lock:
        now = time.monotonic()
        if _cache is not None and (now - _cache.loaded_at) < _CACHE_TTL:
            return _cache

        urls = {
            "twse_val": "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
            "tpex_val": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
            "twse_rev": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            "tpex_rev": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
            "twse_mgn": "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
            "tpex_mgn": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance",
        }
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {k: pool.submit(_fetch_list, u) for k, u in urls.items()}
            raw = {k: f.result() for k, f in futs.items()}

        val_map: dict[str, Valuation] = {}
        for row in raw["twse_val"]:
            code = str(row.get("Code", "")).strip().upper()
            if code:
                val_map[code] = Valuation(
                    pe=_fnum(row.get("PEratio")),
                    pb=_fnum(row.get("PBratio")),
                    yield_pct=_fnum(row.get("DividendYield")),
                )
        for row in raw["tpex_val"]:
            code = str(row.get("SecuritiesCompanyCode", "")).strip().upper()
            if code and code not in val_map:
                val_map[code] = Valuation(
                    pe=_fnum(row.get("PriceEarningRatio")),
                    pb=_fnum(row.get("PriceBookRatio")),
                    yield_pct=_fnum(row.get("YieldRatio")),
                )

        rev_map: dict[str, MonthlyRevenue] = {}
        for src in (raw["twse_rev"], raw["tpex_rev"]):
            for row in src:
                code = str(row.get("公司代號", "")).strip().upper()
                if not code or code in rev_map:
                    continue
                rev_k = _fnum(row.get("營業收入-當月營收"))
                rev_map[code] = MonthlyRevenue(
                    period=_roc_ym(row.get("資料年月", "")),
                    revenue_yi=(rev_k / 100_000.0) if rev_k is not None else None,
                    mom_pct=_fnum(row.get("營業收入-上月比較增減(%)")),
                    yoy_pct=_fnum(row.get("營業收入-去年同月增減(%)")),
                    cum_yoy_pct=_fnum(row.get("累計營業收入-前期比較增減(%)")),
                )

        mgn_map: dict[str, MarginInfo] = {}
        for row in raw["twse_mgn"]:
            code = str(row.get("股票代號", "")).strip().upper()
            if not code:
                continue
            m_cur, m_prev = _fnum(row.get("融資今日餘額")), _fnum(row.get("融資前日餘額"))
            s_cur, s_prev = _fnum(row.get("融券今日餘額")), _fnum(row.get("融券前日餘額"))
            ratio = (s_cur / m_cur * 100.0) if (m_cur and s_cur is not None and m_cur > 0) else None
            mgn_map[code] = MarginInfo(
                margin_balance=m_cur,
                margin_change=(m_cur - m_prev) if (m_cur is not None and m_prev is not None) else None,
                short_balance=s_cur,
                short_change=(s_cur - s_prev) if (s_cur is not None and s_prev is not None) else None,
                short_ratio_pct=ratio,
            )
        for row in raw["tpex_mgn"]:
            code = str(row.get("SecuritiesCompanyCode", "")).strip().upper()
            if not code or code in mgn_map:
                continue
            m_cur, m_prev = _fnum(row.get("MarginPurchaseBalance")), _fnum(row.get("MarginPurchaseBalancePreviousDay"))
            s_cur, s_prev = _fnum(row.get("ShortSaleBalance")), _fnum(row.get("ShortSaleBalancePreviousDay"))
            ratio = (s_cur / m_cur * 100.0) if (m_cur and s_cur is not None and m_cur > 0) else None
            mgn_map[code] = MarginInfo(
                margin_balance=m_cur,
                margin_change=(m_cur - m_prev) if (m_cur is not None and m_prev is not None) else None,
                short_balance=s_cur,
                short_change=(s_cur - s_prev) if (s_cur is not None and s_prev is not None) else None,
                short_ratio_pct=ratio,
            )

        if val_map or rev_map or mgn_map:
            _cache = _TwFundCache(val_map, rev_map, mgn_map, time.monotonic())
            return _cache
        if _cache is not None:
            return _cache
        return _TwFundCache({}, {}, {}, 0.0)


def lookup_valuation(code: str) -> Valuation | None:
    """Quick cached lookup of Taiwan PE/PB/Yield for quote cards."""
    if _cache is None:
        return None
    return _cache.valuation.get(code.strip().upper())


def fetch(quote: StockQuote) -> FundamentalReport:
    """Fetch fundamentals/valuation/revenue/margin for Taiwan or US stocks."""
    if quote.asset_type != "stock":
        raise FundamentalDataUnavailable(quote.ticker)

    if quote.is_taiwan:
        c = _load_tw_cache()
        code = quote.ticker.upper()
        val = c.valuation.get(code)
        rev = c.revenue.get(code)
        mgn = c.margin.get(code)
        if not any((val, rev, mgn)):
            raise FundamentalDataUnavailable(quote.ticker)
        return FundamentalReport(quote=quote, valuation=val, revenue=rev, margin=mgn)

    # US stock fundamentals via yfinance .info
    try:
        info = yf.Ticker(quote.yahoo_symbol).info or {}
    except Exception as err:
        raise FundamentalDataUnavailable(quote.ticker) from err

    val = Valuation(
        pe=_fnum(info.get("trailingPE")),
        pb=_fnum(info.get("priceToBook")),
        yield_pct=(
            float(info["dividendYield"])
            if info.get("dividendYield") is not None and float(info["dividendYield"]) > 0.5
            else (float(info["dividendYield"]) * 100.0 if info.get("dividendYield") else None)
        ),
    )
    rev_g = info.get("revenueGrowth")
    pm = info.get("profitMargins")
    report = FundamentalReport(
        quote=quote,
        valuation=val,
        market_cap=_fnum(info.get("marketCap")),
        forward_pe=_fnum(info.get("forwardPE")),
        rev_growth_pct=(float(rev_g) * 100.0) if rev_g is not None else None,
        profit_margin_pct=(float(pm) * 100.0) if pm is not None else None,
        week52_high=_fnum(info.get("fiftyTwoWeekHigh")),
        week52_low=_fnum(info.get("fiftyTwoWeekLow")),
    )
    if not any((val.pe, val.pb, report.market_cap, report.week52_high)):
        raise FundamentalDataUnavailable(quote.ticker)
    return report

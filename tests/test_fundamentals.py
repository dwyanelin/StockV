"""Unit tests for app.stock.fundamentals (split TWSE/TPEx caches, budget timeout, partial TTL, US)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.stock import fundamentals
from app.stock.data import StockQuote


@pytest.fixture(autouse=True)
def _reset_cache():
    fundamentals.clear_cache()
    yield
    fundamentals.clear_cache()


def test_fnum_and_roc_ym():
    assert fundamentals._fnum("1,234.5") == 1234.5
    assert fundamentals._fnum(None) is None
    assert fundamentals._fnum("--") is None
    assert fundamentals._fnum("N/A") is None
    assert fundamentals._fnum("not-a-num") is None

    assert fundamentals._roc_ym("11510") == "2026/10"
    assert fundamentals._roc_ym("2026/10") == "2026/10"


def test_fetch_list_handles_valid_non_list_and_error(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = [{"Code": "2330"}]
    monkeypatch.setattr(fundamentals.requests, "get", MagicMock(return_value=resp))
    assert fundamentals._fetch_list("https://example.test") == [{"Code": "2330"}]

    resp.json.return_value = {"not": "list"}
    assert fundamentals._fetch_list("https://example.test") == []

    monkeypatch.setattr(fundamentals.requests, "get", MagicMock(side_effect=RuntimeError("timeout")))
    assert fundamentals._fetch_list("https://example.test") == []


def test_load_twse_and_tpex_caches_separately(monkeypatch):
    assert fundamentals.lookup_valuation("2330") is None
    assert fundamentals.is_cache_complete("2330") is True

    called_urls: list[str] = []
    payloads = {
        "BWIBBU_ALL": [{"Code": "2330", "PEratio": "25.5", "PBratio": "6.2", "DividendYield": "1.8"}],
        "tpex_mainboard_peratio_analysis": [
            {"SecuritiesCompanyCode": "8069", "PriceEarningRatio": "20.1", "PriceBookRatio": "4.0", "YieldRatio": "3.1"},
        ],
        "t187ap05_L": [
            {
                "公司代號": "2330",
                "資料年月": "11510",
                "營業收入-當月營收": "300,000,000",
                "營業收入-上月比較增減(%)": "5.2",
                "營業收入-去年同月增減(%)": "28.4",
                "累計營業收入-前期比較增減(%)": "31.0",
            },
            {"公司代號": ""},
        ],
        "mopsfin_t187ap05_O": [
            {"公司代號": "8069", "資料年月": "11510", "營業收入-當月營收": "2,500,000"},
        ],
        "MI_MARGN": [
            {
                "股票代號": "2330",
                "融資今日餘額": "10,000",
                "融資前日餘額": "9,500",
                "融券今日餘額": "500",
                "融券前日餘額": "450",
            },
            {"股票代號": ""},
        ],
        "tpex_mainboard_margin_balance": [
            {
                "SecuritiesCompanyCode": "8069",
                "MarginPurchaseBalance": "2,000",
                "MarginPurchaseBalancePreviousDay": "2,100",
                "ShortSaleBalance": "100",
                "ShortSaleBalancePreviousDay": "90",
            },
            {"SecuritiesCompanyCode": ""},
        ],
    }

    def fake_fetch_list(url: str):
        called_urls.append(url)
        for key, val in payloads.items():
            if key in url:
                return val
        return []

    monkeypatch.setattr(fundamentals, "_fetch_list", fake_fetch_list)

    # Loading TWSE only calls TWSE endpoints (never TPEx)
    twse = fundamentals._load_market_cache("TWSE")
    assert all("twse.com.tw" in u for u in called_urls)
    assert twse.is_complete is True
    assert twse.ttl == fundamentals._CACHE_TTL
    assert twse.valuation["2330"].pe == 25.5
    assert twse.revenue["2330"].period == "2026/10"
    assert twse.revenue["2330"].revenue_yi == pytest.approx(3000.0)
    assert twse.margin["2330"].margin_change == 500.0
    assert twse.margin["2330"].short_ratio_pct == pytest.approx(5.0)
    assert fundamentals.lookup_valuation(" 2330 ").pb == 6.2
    assert fundamentals.is_cache_complete("2330") is True

    # Cache hit
    assert fundamentals._load_market_cache("TWSE") is twse

    # Loading TPEx independently
    called_urls.clear()
    tpex = fundamentals._load_market_cache("TPEX")
    assert all("tpex.org.tw" in u for u in called_urls)
    assert tpex.is_complete is True
    assert tpex.valuation["8069"].pe == 20.1
    assert tpex.margin["8069"].margin_change == -100.0
    assert fundamentals.lookup_valuation("8069").yield_pct == 3.1

    # Expire TWSE cache + empty upstream -> falls back to stale cache
    fundamentals._caches["TWSE"].loaded_at = -999999.0
    monkeypatch.setattr(fundamentals, "_fetch_list", lambda url: [])
    assert fundamentals._load_market_cache("TWSE") is twse

    # No cache + empty upstream -> empty cache
    fundamentals.clear_cache()
    empty_cache = fundamentals._load_market_cache("TWSE")
    assert empty_cache.valuation == {}
    assert empty_cache.is_complete is False


def test_partial_timeout_uses_short_ttl_and_marks_incomplete(monkeypatch):
    def fake_fetch(url: str):
        if "MI_MARGN" in url:
            time.sleep(0.3)
            return [{"股票代號": "2330"}]
        if "BWIBBU_ALL" in url:
            return [{"Code": "2330", "PEratio": "20.0", "PBratio": "5.0", "DividendYield": "2.0"}]
        return [{"公司代號": "2330", "資料年月": "11510", "營業收入-當月營收": "100,000"}]

    monkeypatch.setattr(fundamentals, "_fetch_list", fake_fetch)
    monkeypatch.setattr(fundamentals, "_FETCH_BUDGET_SECONDS", 0.05)

    c = fundamentals._load_market_cache("TWSE")
    assert c.is_complete is False
    assert c.ttl == fundamentals._PARTIAL_CACHE_TTL
    assert c.valuation["2330"].pe == 20.0
    assert "2330" not in c.margin
    assert fundamentals.is_cache_complete("2330") is False


def test_lock_timeout_raises_busy(monkeypatch):
    monkeypatch.setattr(fundamentals, "_LOCK_TIMEOUT_SECONDS", 0.01)
    lock = fundamentals._locks["TWSE"]
    lock.acquire()
    try:
        with pytest.raises(fundamentals.FundamentalDataBusy):
            fundamentals._load_market_cache("TWSE")
    finally:
        lock.release()


def test_fetch_taiwan_us_and_errors(monkeypatch):
    idx_q = StockQuote("台股", "^TWII", "加權指數", True, "TWD", "index")
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(idx_q)

    # Taiwan listed stock found
    tw_q = StockQuote("2330", "2330.TW", "台積電", True, "TWD", "stock")
    fundamentals._caches["TWSE"] = fundamentals._TwFundCache(
        valuation={"2330": fundamentals.Valuation(pe=20.0, pb=5.0, yield_pct=2.0)},
        revenue={},
        margin={},
        loaded_at=time.monotonic() + 1000,
    )
    rep_tw = fundamentals.fetch(tw_q)
    assert rep_tw.valuation.pe == 20.0
    assert rep_tw.is_complete is True

    # Taiwan stock missing
    tw_missing = StockQuote("9999", "9999.TW", "無資料", True, "TWD", "stock")
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(tw_missing)

    # US stock found (dividendYield > 0.5 treated as already %)
    us_q = StockQuote("AAPL", "AAPL", "Apple", False, "USD", "stock")
    tk = MagicMock()
    tk.info = {
        "trailingPE": 30.0,
        "priceToBook": 40.0,
        "dividendYield": 0.55,
        "marketCap": 3e12,
        "forwardPE": 28.0,
        "revenueGrowth": 0.08,
        "profitMargins": 0.25,
        "fiftyTwoWeekHigh": 250.0,
        "fiftyTwoWeekLow": 170.0,
    }
    monkeypatch.setattr(fundamentals.yf, "Ticker", lambda sym: tk)
    rep_us = fundamentals.fetch(us_q)
    assert rep_us.valuation.yield_pct == pytest.approx(0.55)
    assert rep_us.rev_growth_pct == pytest.approx(8.0)

    # US stock with decimal dividendYield (<= 0.5 multiplied by 100)
    tk.info = {"trailingPE": 20.0, "dividendYield": 0.025}
    rep_us2 = fundamentals.fetch(us_q)
    assert rep_us2.valuation.yield_pct == pytest.approx(2.5)

    # US stock with empty info raises
    tk.info = {}
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(us_q)

    # US yfinance exception raises
    monkeypatch.setattr(fundamentals.yf, "Ticker", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(us_q)

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


def test_yf_quality_metrics_cache_timeout_and_financial_guard(monkeypatch):
    # Normal stock extraction
    roe, ev, mcap, is_fin = fundamentals._extract_yf_quality(
        {
            "sector": "Technology",
            "returnOnEquity": 0.4186,
            "enterpriseToEbitda": 14.896,
            "marketCap": 53e12,
        }
    )
    assert roe == pytest.approx(41.86)
    assert ev == pytest.approx(14.896)
    assert mcap == pytest.approx(53e12)
    assert is_fin is False

    # Financial Services sector suppresses EV/EBITDA
    roe_f, ev_f, _, is_fin_f = fundamentals._extract_yf_quality(
        {
            "sector": "Financial Services",
            "returnOnEquity": 0.26,
            "enterpriseToEbitda": 5.2,
            "marketCap": 1.5e12,
        }
    )
    assert roe_f == pytest.approx(26.0)
    assert ev_f is None
    assert is_fin_f is True

    # Fetch + 6h cache hit + eviction
    calls = {"n": 0}

    def fake_info(sym: str):
        calls["n"] += 1
        return {"sector": "Technology", "returnOnEquity": 0.25, "enterpriseToEbitda": 12.0, "marketCap": 1e11}

    monkeypatch.setattr(fundamentals, "_get_yf_info", fake_info)
    monkeypatch.setattr(fundamentals, "_YF_CACHE_MAX_ENTRIES", 1)
    m1, ok1 = fundamentals._fetch_yf_metrics("2330.TW")
    assert ok1 is True and m1.roe_pct == pytest.approx(25.0)
    m1_cached, ok1_cached = fundamentals._fetch_yf_metrics("2330.TW")
    assert ok1_cached is True and m1_cached is m1 and calls["n"] == 1
    # Adding a second symbol evicts oldest
    m2, ok2 = fundamentals._fetch_yf_metrics("3406.TW")
    assert ok2 is True and m2 is not None and "2330.TW" not in fundamentals._yf_cache

    # Timeout degrades gracefully
    fundamentals.clear_cache()

    def slow_info(sym: str):
        time.sleep(0.25)
        return {"returnOnEquity": 0.1}

    monkeypatch.setattr(fundamentals, "_get_yf_info", slow_info)
    m_to, ok_to = fundamentals._fetch_yf_metrics("2330.TW", budget=0.03)
    assert m_to is None and ok_to is False

    # Exception degrades gracefully
    monkeypatch.setattr(fundamentals, "_get_yf_info", MagicMock(side_effect=RuntimeError("429")))
    m_err, ok_err = fundamentals._fetch_yf_metrics("2330.TW")
    assert m_err is None and ok_err is False

    # Empty dict degrades gracefully
    monkeypatch.setattr(fundamentals, "_get_yf_info", lambda sym: {})
    m_empty, ok_empty = fundamentals._fetch_yf_metrics("2330.TW")
    assert m_empty is None and ok_empty is False


def test_fetch_taiwan_us_and_errors(monkeypatch):
    idx_q = StockQuote("台股", "^TWII", "加權指數", True, "TWD", "index")
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(idx_q)

    # Taiwan listed stock found + Yahoo .info ROE/EV/EBITDA enriched in parallel
    tw_q = StockQuote("2330", "2330.TW", "台積電", True, "TWD", "stock")
    fundamentals._caches["TWSE"] = fundamentals._TwFundCache(
        valuation={"2330": fundamentals.Valuation(pe=20.0, pb=5.0, yield_pct=2.0)},
        revenue={},
        margin={},
        loaded_at=time.monotonic() + 1000,
    )
    monkeypatch.setattr(
        fundamentals,
        "_get_yf_info",
        lambda sym: {"sector": "Technology", "returnOnEquity": 0.42, "enterpriseToEbitda": 15.0, "marketCap": 50e12},
    )
    rep_tw = fundamentals.fetch(tw_q)
    assert rep_tw.valuation.pe == 20.0
    assert rep_tw.roe_pct == pytest.approx(42.0)
    assert rep_tw.ev_ebitda == pytest.approx(15.0)
    assert rep_tw.market_cap == pytest.approx(50e12)
    assert rep_tw.is_complete is True
    assert fundamentals.is_cache_complete("2330") is True

    # Taiwan stock when Yahoo .info fails -> graceful degradation + marked incomplete
    fundamentals.clear_cache()
    fundamentals._caches["TWSE"] = fundamentals._TwFundCache(
        valuation={"2330": fundamentals.Valuation(pe=20.0, pb=5.0, yield_pct=2.0)},
        revenue={},
        margin={},
        loaded_at=time.monotonic() + 1000,
    )
    monkeypatch.setattr(fundamentals, "_get_yf_info", MagicMock(side_effect=RuntimeError("rate limit")))
    rep_partial = fundamentals.fetch(tw_q)
    assert rep_partial.valuation.pe == 20.0
    assert rep_partial.roe_pct is None
    assert rep_partial.is_complete is False
    assert fundamentals.is_cache_complete("2330") is False

    # Taiwan stock missing from both market cache and Yahoo
    tw_missing = StockQuote("9999", "9999.TW", "無資料", True, "TWD", "stock")
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(tw_missing)

    # US stock found (dividendYield > 0.5 treated as already %, plus ROE & EV/EBITDA)
    us_q = StockQuote("AAPL", "AAPL", "Apple", False, "USD", "stock")
    monkeypatch.setattr(
        fundamentals,
        "_get_yf_info",
        lambda sym: {
            "sector": "Technology",
            "trailingPE": 30.0,
            "priceToBook": 40.0,
            "dividendYield": 0.55,
            "returnOnEquity": 1.42,
            "enterpriseToEbitda": 30.6,
            "marketCap": 3e12,
            "forwardPE": 28.0,
            "revenueGrowth": 0.08,
            "profitMargins": 0.25,
            "fiftyTwoWeekHigh": 250.0,
            "fiftyTwoWeekLow": 170.0,
        },
    )
    rep_us = fundamentals.fetch(us_q)
    assert rep_us.valuation.yield_pct == pytest.approx(0.55)
    assert rep_us.roe_pct == pytest.approx(142.0)
    assert rep_us.ev_ebitda == pytest.approx(30.6)
    assert rep_us.rev_growth_pct == pytest.approx(8.0)

    # US stock with decimal dividendYield (<= 0.5 multiplied by 100)
    monkeypatch.setattr(fundamentals, "_get_yf_info", lambda sym: {"trailingPE": 20.0, "dividendYield": 0.025})
    rep_us2 = fundamentals.fetch(us_q)
    assert rep_us2.valuation.yield_pct == pytest.approx(2.5)

    # US stock with empty info raises
    monkeypatch.setattr(fundamentals, "_get_yf_info", lambda sym: {})
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(us_q)

    # US yfinance exception raises
    monkeypatch.setattr(fundamentals, "_get_yf_info", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(fundamentals.FundamentalDataUnavailable):
        fundamentals.fetch(us_q)

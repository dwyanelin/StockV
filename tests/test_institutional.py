"""Unit tests for app.stock.institutional (TWSE T86/MI_QFIIS/BFI82U and TPEx OTC)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.stock import institutional


@pytest.fixture(autouse=True)
def _clear_caches():
    institutional._t86_cache.clear()
    institutional._tpex_cache.clear()
    institutional._bfi82u_cache.clear()
    yield
    institutional._t86_cache.clear()
    institutional._tpex_cache.clear()
    institutional._bfi82u_cache.clear()


def test_get_json_num_and_trim_cache(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = {"stat": "OK", "data": [[1]]}
    monkeypatch.setattr(institutional.requests, "get", MagicMock(return_value=resp))
    assert institutional._get_json("https://example.test") == {"stat": "OK", "data": [[1]]}

    resp.json.return_value = {"stat": "NO DATA", "data": []}
    assert institutional._get_json("https://example.test") is None

    monkeypatch.setattr(institutional.requests, "get", MagicMock(side_effect=RuntimeError("net")))
    assert institutional._get_json("https://example.test") is None

    assert institutional._num("1,234") == 1234.0
    assert institutional._num("") == 0.0
    assert institutional._num("bad") == 0.0

    cache = {date(2026, 1, i): i for i in range(1, 6)}
    institutional._trim_cache(cache, limit=3)
    assert len(cache) == 3
    assert date(2026, 1, 1) not in cache


def test_t86_table_parsing_and_caching(monkeypatch):
    d = date(2026, 11, 20)
    payload = {
        "stat": "OK",
        "fields": [
            "證券代號",
            "證券名稱",
            "外陸資買賣超股數(不含外資自營商)",
            "投信買賣超股數",
            "自營商買賣超股數",
            "三大法人買賣超股數",
        ],
        "data": [["2330", "台積電", "10,000", "2,000", "-1,000", "11,000"]],
    }
    mock_get = MagicMock(return_value=payload)
    monkeypatch.setattr(institutional, "_get_json", mock_get)

    table = institutional._t86_table(d)
    assert table["2330"] == ("台積電", 10000.0, 2000.0, -1000.0, 11000.0)
    # Cache hit
    assert institutional._t86_table(d) is table
    assert mock_get.call_count == 1


def test_tpex_table_parsing_and_caching(monkeypatch):
    d = date(2026, 11, 20)
    row = [""] * 24
    row[0], row[1] = "8069", "元太"
    row[10], row[13], row[22], row[23] = "5,000", "1,000", "-500", "5,500"
    payload = {"stat": "OK", "tables": [{"data": [["short"], row]}]}
    mock_get = MagicMock(return_value=payload)
    monkeypatch.setattr(institutional, "_get_json", mock_get)

    table = institutional._tpex_table(d)
    assert table == {"8069": ("元太", 5000.0, 1000.0, -500.0, 5500.0)}
    assert institutional._tpex_table(d) is table
    assert mock_get.call_count == 1


def test_bfi82u_day_parsing_and_total_fallback(monkeypatch):
    d = date(2026, 11, 20)
    payload = {
        "stat": "OK",
        "data": [
            ["short"],
            ["外資及陸資(不含外資自營商)", "0", "0", "100,000,000"],
            ["投信", "0", "0", "20,000,000"],
            ["自營商(自行買賣)", "0", "0", "-10,000,000"],
            ["合計", "0", "0", "0"],  # triggers computed fallback
        ],
    }
    monkeypatch.setattr(institutional, "_get_json", lambda url: payload)
    entry = institutional._bfi82u_day(d)
    assert entry == (100_000_000.0, 20_000_000.0, -10_000_000.0, 110_000_000.0)
    # Cache hit
    assert institutional._bfi82u_day(d) == entry

    # Explicit total branch
    d2 = date(2026, 11, 19)
    monkeypatch.setattr(
        institutional,
        "_get_json",
        lambda url: {"stat": "OK", "data": [["合計", "0", "0", "50,000,000"]]},
    )
    assert institutional._bfi82u_day(d2) == (0.0, 0.0, 0.0, 50_000_000.0)


def test_foreign_holding_found_missing_and_bad_fields(monkeypatch):
    monkeypatch.setattr(
        institutional,
        "_get_json",
        lambda url: {
            "stat": "OK",
            "fields": ["證券代號", "全體外資及陸資持股比率"],
            "data": [["2330", "73.45"]],
        },
    )
    res = institutional._foreign_holding("2330")
    assert res is not None and res[0] == 73.45
    assert institutional._foreign_holding("9999") is None

    monkeypatch.setattr(
        institutional,
        "_get_json",
        lambda url: {"stat": "OK", "fields": ["其他"], "data": [["x"]]},
    )
    assert institutional._foreign_holding("2330") is None

    monkeypatch.setattr(institutional, "_get_json", lambda url: None)
    assert institutional._foreign_holding("2330") is None


def test_fetch_market_and_fetch_twse_tpex_and_errors(monkeypatch):
    d1, d2 = date(2026, 11, 19), date(2026, 11, 20)
    monkeypatch.setattr(institutional, "_recent_weekdays", lambda n: [d2, d1])

    # Market success
    monkeypatch.setattr(
        institutional,
        "_bfi82u_day",
        lambda d: (1e8, 2e8, -0.5e8, 2.5e8) if d == d2 else None,
    )
    mkt = institutional.fetch_market(num_days=5)
    assert mkt.market == "MARKET"
    assert len(mkt.days) == 1
    assert mkt.days[0].day == d2

    # Market empty raises
    monkeypatch.setattr(institutional, "_bfi82u_day", lambda d: None)
    with pytest.raises(institutional.InstitutionalDataUnavailable):
        institutional.fetch_market()

    # TWSE stock success with foreign holding
    monkeypatch.setattr(
        institutional,
        "_t86_table",
        lambda d: {"2330": ("台積電", 1000.0, 200.0, 50.0, 1250.0)} if d == d2 else None,
    )
    monkeypatch.setattr(institutional, "_foreign_holding", lambda t: (73.5, d2))
    rep = institutional.fetch("2330")
    assert rep.market == "TWSE"
    assert rep.name == "台積電"
    assert rep.foreign_holding_pct == 73.5

    # Explicit TPEX
    monkeypatch.setattr(
        institutional,
        "_tpex_table",
        lambda d: {"8069": ("元太", 500.0, 100.0, -50.0, 550.0)} if d == d2 else None,
    )
    rep_tpex = institutional.fetch("8069", market="TPEX")
    assert rep_tpex.market == "TPEX"
    assert rep_tpex.name == "元太"

    # TWSE miss -> TPEx fallback
    monkeypatch.setattr(institutional, "_t86_table", lambda d: None)
    rep_fb = institutional.fetch("8069")
    assert rep_fb.market == "TPEX"

    # Neither TWSE nor TPEx -> raises
    monkeypatch.setattr(institutional, "_tpex_table", lambda d: None)
    with pytest.raises(institutional.InstitutionalDataUnavailable):
        institutional.fetch("0000")

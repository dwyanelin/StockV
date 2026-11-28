"""Unit tests for app.stock.tdcc (CSV parsing, WoW merge, GCS snapshot loader & caching)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app import config
from app.stock import tdcc


@pytest.fixture(autouse=True)
def _reset_tdcc():
    tdcc.clear_cache()
    yield
    tdcc.clear_cache()


def test_helpers_and_parse_tdcc_csv():
    assert tdcc._fmt_ymd("20261127") == "2026/11/27"
    assert tdcc._fmt_ymd("2026/11/27") == "2026/11/27"
    assert tdcc._to_float("84.09") == pytest.approx(84.09)
    assert tdcc._to_float("bad") == 0.0
    assert tdcc._to_int("3,381,152") == 3381152
    assert tdcc._to_int("bad") == 0

    sample_csv = "\ufeff資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n" \
        "20261127,2330  ,1,2788390,332366639,1.28\n" \
        "20261127,2330  ,12,584,282892956,1.09\n" \
        "20261127,2330  ,13,344,238468856,0.91\n" \
        "20261127,2330  ,14,220,197539731,0.76\n" \
        "20261127,2330  ,15,1464,21808829716,84.09\n" \
        "20261127,2330  ,17,3381152,25932370067,100.00\n" \
        "20261127,000218,17,0,0,0.00\n" \
        "20261127, ,15,1,100,1.0\n" \
        "short,row\n"

    ymd, stocks = tdcc.parse_tdcc_csv(sample_csv)
    assert ymd == "20261127"
    assert "000218" not in stocks
    assert stocks["2330"] == (84.09, 86.85, 3381152)


def test_merge_snapshot_new_week_and_idempotent_rerun():
    week1 = tdcc.merge_snapshot("20261120", {"2330": (83.5, 86.0, 3400000)}, existing=None)
    assert week1 == {
        "date": "20261120",
        "prev_date": "",
        "stocks": {"2330": [83.5, 86.0, 3400000]},
    }

    # New week shifts week1 into prev slots
    week2 = tdcc.merge_snapshot(
        "20261127",
        {"2330": (84.09, 86.85, 3381152), "7822": (45.0, 52.0, 5000)},
        existing=week1,
    )
    assert week2["date"] == "20261127"
    assert week2["prev_date"] == "20261120"
    assert week2["stocks"]["2330"] == [84.09, 86.85, 3381152, 83.5, 86.0, 3400000]
    assert week2["stocks"]["7822"] == [45.0, 52.0, 5000]

    # Re-running on same week preserves prev_date and previous values (idempotent)
    week2_rerun = tdcc.merge_snapshot("20261127", {"2330": (84.10, 86.86, 3381150)}, existing=week2)
    assert week2_rerun["prev_date"] == "20261120"
    assert week2_rerun["stocks"]["2330"] == [84.10, 86.86, 3381150, 83.5, 86.0, 3400000]


def test_parse_snapshot_json_and_lookup_caching(monkeypatch):
    assert tdcc._parse_snapshot_json({"date": "", "stocks": {}}) == {}
    assert tdcc._parse_snapshot_json({"date": "20261127", "stocks": {"BAD": [1, 2]}}) == {}

    payload = {
        "date": "20261127",
        "prev_date": "20261120",
        "stocks": {
            "2330": [84.09, 86.85, 3381152, 83.50, 86.00, 3400000],
            "7822": [45.0, 52.0, 5000],
        },
    }
    resp = MagicMock()
    resp.json.return_value = payload
    get_mock = MagicMock(return_value=resp)
    monkeypatch.setattr(tdcc.requests, "get", get_mock)

    assert tdcc.lookup("  ") is None
    s = tdcc.lookup("2330")
    assert s is not None
    assert s.date_str == "2026/11/27"
    assert s.whale_1000_pct == 84.09
    assert s.whale_1000_chg == pytest.approx(0.59)
    assert s.whale_400_chg == pytest.approx(0.85)
    assert s.shareholders_chg == -18848

    # Cache hit does not re-fetch
    s2 = tdcc.lookup("7822")
    assert s2 is not None and s2.whale_1000_chg is None
    assert get_mock.call_count == 1

    # Expired cache + upstream error -> falls back to stale cache
    tdcc._cache.loaded_at = -999999.0
    monkeypatch.setattr(tdcc.requests, "get", MagicMock(side_effect=RuntimeError("net")))
    assert tdcc.lookup("2330").whale_1000_pct == 84.09

    # Empty cache + upstream error -> returns None and caches short failure TTL
    tdcc.clear_cache()
    assert tdcc.lookup("2330") is None
    assert tdcc._cache is not None and tdcc._cache.ttl == tdcc._FAIL_RETRY_TTL

    # Empty snapshot URL returns None cleanly
    tdcc.clear_cache()
    monkeypatch.setattr(config, "TDCC_SNAPSHOT_URL", "")
    assert tdcc.lookup("2330") is None

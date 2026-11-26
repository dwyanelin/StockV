"""Unit tests for app.stock.futures (TAIFEX day/night quotes & 1M intraday) and data integration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.stock import aliases, data, futures

_TW_TZ = timezone(timedelta(hours=8))


def test_fnum_and_preferred_sessions():
    assert futures._fnum("39,275.00") == 39275.0
    assert futures._fnum(None) is None
    assert futures._fnum("--") is None
    assert futures._fnum("bad") is None

    day_dt = datetime(2026, 11, 26, 10, 30, tzinfo=_TW_TZ)
    assert futures._preferred_sessions(day_dt) == (("0", "日盤"), ("1", "夜盤"))

    night_dt = datetime(2026, 11, 26, 17, 15, tzinfo=_TW_TZ)
    assert futures._preferred_sessions(night_dt) == (("1", "夜盤"), ("0", "日盤"))

    after_midnight = datetime(2026, 11, 27, 2, 0, tzinfo=_TW_TZ)
    assert futures._preferred_sessions(after_midnight) == (("1", "夜盤"), ("0", "日盤"))


def test_post_handles_valid_non_dict_and_error(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = {"RtData": {}}
    monkeypatch.setattr(futures.requests, "post", MagicMock(return_value=resp))
    assert futures._post("getQuoteList", {}) == {"RtData": {}}

    resp.json.return_value = ["not-a-dict"]
    assert futures._post("getQuoteList", {}) is None

    monkeypatch.setattr(futures.requests, "post", MagicMock(side_effect=RuntimeError("net")))
    assert futures._post("getQuoteList", {}) is None


def test_fetch_quote_and_fallback_sessions(monkeypatch):
    sample = {
        "RtData": {
            "QuoteList": [
                {"SymbolID": "TXF-S", "DispCName": "臺指現貨", "CLastPrice": "39151.81"},
                {
                    "SymbolID": "TXFL6-M",
                    "DispCName": "臺指期126",
                    "CLastPrice": "39275.00",
                    "COpenPrice": "39250.00",
                    "CHighPrice": "39559.00",
                    "CLowPrice": "39112.00",
                    "CRefPrice": "39492.00",
                    "CTotalVolume": "7202",
                    "CTime": "171843",
                },
            ]
        }
    }
    monkeypatch.setattr(futures, "_post", MagicMock(return_value=sample))
    now = datetime(2026, 11, 26, 17, 20, tzinfo=_TW_TZ)
    q = futures.fetch_quote("TXF", now=now)
    assert q["symbol_id"] == "TXFL6-M"
    assert q["name"] == "臺指期126 (夜盤)"
    assert q["price"] == 39275.0
    assert q["prev_close"] == 39492.0
    assert q["as_of"] == "2026/11/26 17:18"

    # Non-6-digit CTime fallback
    sample["RtData"]["QuoteList"][1]["CTime"] = ""
    q2 = futures.fetch_quote("TXF", now=now)
    assert q2["as_of"] == "2026/11/26 17:20"

    # Empty QuoteList raises
    monkeypatch.setattr(futures, "_post", MagicMock(return_value={"RtData": {"QuoteList": []}}))
    with pytest.raises(futures.FuturesDataUnavailable):
        futures.fetch_quote("TXF", now=now)


def test_fetch_intraday_handles_night_midnight_wrap_and_errors(monkeypatch):
    q_dict = {
        "symbol_id": "TXFL6-M",
        "name": "臺指期126 (夜盤)",
        "session": "夜盤",
        "price": 39275.0,
        "prev_close": 39492.0,
        "open": 39250.0,
        "high": 39559.0,
        "low": 39112.0,
        "volume": 7202.0,
        "as_of": "2026/11/27 01:05",
    }
    monkeypatch.setattr(futures, "fetch_quote", MagicMock(return_value=q_dict))

    ticks = {
        "RtData": {
            "Ticks": [
                ["150100", "39250", "39273", "39215", "39258", "365"],
                ["000500", "39260", "39280", "39250", "39275", "50"],
                ["bad", "1", "2", "3", "4", "5"],
                ["150200", "", "2", "3", "4", "5"],
                "not-a-list",
            ]
        }
    }
    monkeypatch.setattr(futures, "_post", MagicMock(return_value=ticks))
    after_midnight = datetime(2026, 11, 27, 1, 5, tzinfo=_TW_TZ)
    q_out, df, prev = futures.fetch_intraday("TXF", now=after_midnight)
    assert q_out["symbol_id"] == "TXFL6-M"
    assert prev == 39492.0
    assert len(df) == 2
    assert df.index[0].day == 26  # 15:01 belongs to Nov 26
    assert df.index[1].day == 27  # 00:05 wrapped to Nov 27

    # Empty ticks raises
    monkeypatch.setattr(futures, "_post", MagicMock(return_value={"RtData": {"Ticks": []}}))
    with pytest.raises(futures.FuturesDataUnavailable):
        futures.fetch_intraday("TXF", now=after_midnight)

    # All invalid ticks raises
    monkeypatch.setattr(futures, "_post", MagicMock(return_value={"RtData": {"Ticks": [["bad", "1", "2", "3", "4", "5"]]}}))
    with pytest.raises(futures.FuturesDataUnavailable):
        futures.fetch_intraday("TXF", now=after_midnight)


def test_aliases_and_data_integration_for_taifex_and_us_futures(monkeypatch):
    assert aliases.lookup("台指期").yahoo_symbol == "TAIFEX:TXF"
    assert aliases.lookup("小台").yahoo_symbol == "TAIFEX:MXF"
    assert aliases.lookup("小那").yahoo_symbol == "NQ=F"
    assert aliases.lookup("小標普").yahoo_symbol == "ES=F"
    assert aliases.lookup("小道").yahoo_symbol == "YM=F"

    q = data.resolve("台指期")
    assert q.yahoo_symbol == "TAIFEX:TXF"
    assert q.asset_type == "futures"

    # quick_quote delegates to futures.fetch_quote
    fake_q = {
        "symbol_id": "TXFL6-M",
        "name": "臺指期126 (夜盤)",
        "session": "夜盤",
        "price": 39275.0,
        "prev_close": 39492.0,
        "open": 39250.0,
        "high": 39559.0,
        "low": 39112.0,
        "volume": 7202.0,
        "as_of": "2026/11/26 17:20",
    }
    monkeypatch.setattr(futures, "fetch_quote", MagicMock(return_value=fake_q))
    snap = data.quick_quote(q)
    assert snap.price == 39275.0
    assert snap.quote.name == "臺指期126 (夜盤)"

    # quick_quote error maps to StockNotFoundError
    monkeypatch.setattr(futures, "fetch_quote", MagicMock(side_effect=futures.FuturesDataUnavailable("TXF")))
    with pytest.raises(data.StockNotFoundError):
        data.quick_quote(q)

    # intraday delegates to futures.fetch_intraday
    idx = pd.date_range("2026-11-26 15:01", periods=2, freq="min", tz="Asia/Taipei")
    df_in = pd.DataFrame({"Open": [1.0, 2.0], "High": [2.0, 3.0], "Low": [1.0, 2.0], "Close": [1.5, 2.5], "Volume": [10, 20]}, index=idx)
    monkeypatch.setattr(futures, "fetch_intraday", MagicMock(return_value=(fake_q, df_in, 39492.0)))
    df_out, prev = data.intraday(q)
    assert len(df_out) == 2
    assert prev == 39492.0

    # intraday error maps to StockNotFoundError
    monkeypatch.setattr(futures, "fetch_intraday", MagicMock(side_effect=futures.FuturesDataUnavailable("TXF")))
    with pytest.raises(data.StockNotFoundError):
        data.intraday(q)

    # kline_history falls back to ^TWII cleanly
    fake_tk = MagicMock()
    fake_tk.history.return_value = df_in
    monkeypatch.setattr(data.yf, "Ticker", MagicMock(return_value=fake_tk))
    kdf = data.kline_history(q, "D")
    assert len(kdf) == 2

"""Unit tests for app.stock.data (symbol resolution, history, quotes, dividends, FX)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.stock import aliases, data, tw_directory


def _ohlcv_df(dates: list[str], closes: list[float], opens=None, highs=None, lows=None, vols=None) -> pd.DataFrame:
    n = len(dates)
    idx = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "Open": opens if opens is not None else closes,
            "High": highs if highs is not None else [c + 1 for c in closes],
            "Low": lows if lows is not None else [c - 1 for c in closes],
            "Close": closes,
            "Volume": vols if vols is not None else [1000.0] * n,
        },
        index=idx,
    )


def _tw_quote() -> data.StockQuote:
    return data.StockQuote("2330", "2330.TW", "台積電", True, "TWD", "stock")


def _us_quote() -> data.StockQuote:
    return data.StockQuote("AAPL", "AAPL", "Apple Inc.", False, "USD", "stock")


def test_has_data_handles_non_empty_empty_and_exception(monkeypatch):
    tk_ok = MagicMock()
    tk_ok.history.return_value = _ohlcv_df(["2026-11-20"], [100.0])
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk_ok)
    assert data._has_data("2330.TW") is True

    tk_empty = MagicMock()
    tk_empty.history.return_value = pd.DataFrame()
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk_empty)
    assert data._has_data("NOPE") is False

    monkeypatch.setattr(data.yf, "Ticker", MagicMock(side_effect=RuntimeError("net")))
    assert data._has_data("ERR") is False


def test_resolve_empty_alias_directory_cjk_us_and_fallback(monkeypatch):
    with pytest.raises(data.StockNotFoundError):
        data.resolve("   ")

    # 1. Cross-asset alias
    q_twii = data.resolve("台股")
    assert q_twii.yahoo_symbol == "^TWII"
    assert q_twii.asset_type == "index"

    # 2. Taiwan stock directory (primary .TW fails, falls back to .TWO)
    monkeypatch.setattr(
        tw_directory,
        "lookup",
        lambda q: tw_directory.TwStock("8069", "元太", ".TW", "TWSE") if q == "元太" else None,
    )
    monkeypatch.setattr(data, "_has_data", lambda sym: sym == "8069.TWO")
    q_otc = data.resolve("元太")
    assert q_otc.yahoo_symbol == "8069.TWO"
    assert q_otc.is_taiwan is True

    # Directory hit but neither suffix has data -> StockNotFoundError
    monkeypatch.setattr(data, "_has_data", lambda sym: False)
    with pytest.raises(data.StockNotFoundError):
        data.resolve("元太")

    # Unknown Chinese text -> StockNotFoundError without hitting Yahoo
    with pytest.raises(data.StockNotFoundError):
        data.resolve("不存在公司")

    # 3. Direct US ticker with info lookup
    monkeypatch.setattr(data, "_has_data", lambda sym: sym == "AAPL")
    tk_us = MagicMock()
    tk_us.info = {"longName": "Apple Inc.", "currency": "USD"}
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk_us)
    q_us = data.resolve("aapl")
    assert q_us.ticker == "AAPL"
    assert q_us.name == "Apple Inc."
    assert q_us.is_taiwan is False

    # Direct numeric Taiwan fallback when info raises
    monkeypatch.setattr(data, "_has_data", lambda sym: sym == "9999.TWO")
    tk_bad_info = MagicMock()
    type(tk_bad_info).info = property(lambda self: (_ for _ in ()).throw(RuntimeError("info err")))
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk_bad_info)
    q_num = data.resolve("9999")
    assert q_num.yahoo_symbol == "9999.TWO"
    assert q_num.currency == "TWD"
    assert q_num.name == "9999"

    # Completely unknown ASCII symbol
    monkeypatch.setattr(data, "_has_data", lambda sym: False)
    with pytest.raises(data.StockNotFoundError):
        data.resolve("ZZZZZZ")


def test_backfill_latest_bar_edge_cases():
    # Empty df returns immediately
    empty = pd.DataFrame()
    assert data._backfill_latest_bar(MagicMock(), empty).empty

    # 1d history raises, fast_info uses dict .get("lastPrice"), updates Open/High/Low/Close
    df = _ohlcv_df(
        ["2026-11-20"],
        [float("nan")],
        opens=[float("nan")],
        highs=[90.0],
        lows=[110.0],
    )
    tk = MagicMock()
    tk.ticker = "2330.TW"
    tk.history.side_effect = RuntimeError("1d failed")
    tk.fast_info = {"lastPrice": 100.0}
    repaired = data._backfill_latest_bar(tk, df)
    assert repaired["Close"].iloc[-1] == 100.0
    assert repaired["Open"].iloc[-1] == 100.0
    assert repaired["High"].iloc[-1] == 100.0
    assert repaired["Low"].iloc[-1] == 100.0

    # fast_info raises -> still returns df without crashing
    tk_err = MagicMock()
    tk_err.ticker = "2330.TW"
    tk_err.history.return_value = pd.DataFrame()
    type(tk_err).fast_info = property(lambda self: (_ for _ in ()).throw(RuntimeError("fi err")))
    still_nan = data._backfill_latest_bar(tk_err, df)
    assert pd.isna(still_nan["Close"].iloc[-1])


def test_intraday_primary_fallback_and_prev_close(monkeypatch):
    q = _tw_quote()
    # 1m empty -> 5m empty -> 15m has 2 days (should filter to last day)
    df_15m = _ohlcv_df(["2026-11-19 13:00", "2026-11-20 09:00", "2026-11-20 09:15"], [990.0, 1000.0, 1010.0])
    df_daily = _ohlcv_df(["2026-11-19", "2026-11-20"], [995.0, 1010.0])

    tk = MagicMock()
    tk.history.side_effect = [pd.DataFrame(), pd.DataFrame(), df_15m, df_daily]
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk)

    res_df, prev = data.intraday(q)
    assert len(res_df) == 2
    assert prev == 995.0

    # Daily latest date differs from intraday -> uses daily[-1]
    tk2 = MagicMock()
    tk2.history.side_effect = [
        _ohlcv_df(["2026-11-20 09:00"], [1000.0]),
        _ohlcv_df(["2026-11-19"], [980.0]),
    ]
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk2)
    _, prev2 = data.intraday(q)
    assert prev2 == 980.0

    # Prev close lookup raises -> returns None gracefully
    tk3 = MagicMock()
    tk3.history.side_effect = [_ohlcv_df(["2026-11-20 09:00"], [1000.0]), RuntimeError("daily err")]
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk3)
    _, prev3 = data.intraday(q)
    assert prev3 is None

    # All intraday intervals empty -> StockNotFoundError
    tk4 = MagicMock()
    tk4.history.return_value = pd.DataFrame()
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk4)
    with pytest.raises(data.StockNotFoundError):
        data.intraday(q)


def test_kline_history_and_daily_history(monkeypatch):
    q = _tw_quote()
    df = _ohlcv_df(["2026-11-19", "2026-11-20"], [100.0, 105.0])
    tk = MagicMock()
    tk.history.return_value = df
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk)

    out = data.kline_history(q, timeframe="W")
    assert len(out) == 2
    tk.history.assert_called_with(period="2y", interval="1wk")

    # Empty history -> raises
    tk.history.return_value = pd.DataFrame()
    with pytest.raises(data.StockNotFoundError):
        data.kline_history(q, timeframe="D")
    with pytest.raises(data.StockNotFoundError):
        data.daily_history(q)

    # All-NaN history that cannot be backfilled -> raises
    nan_df = _ohlcv_df(["2026-11-20"], [float("nan")])
    tk_nan = MagicMock()
    tk_nan.history.side_effect = [nan_df, pd.DataFrame(), nan_df, pd.DataFrame()]
    tk_nan.fast_info = SimpleNamespace(last_price=None)
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk_nan)
    with pytest.raises(data.StockNotFoundError):
        data.kline_history(q)
    with pytest.raises(data.StockNotFoundError):
        data.daily_history(q)


def test_quick_quote(monkeypatch):
    q = _tw_quote()
    df = _ohlcv_df(["2026-11-19", "2026-11-20"], [100.0, 105.0], vols=[5000.0, 8000.0])
    tk = MagicMock()
    tk.history.return_value = df
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk)

    snap = data.quick_quote(q)
    assert snap.price == 105.0
    assert snap.prev_close == 100.0
    assert snap.volume == 8000.0
    assert snap.as_of == "2026/11/20"

    # Empty history raises
    tk.history.return_value = pd.DataFrame()
    with pytest.raises(data.StockNotFoundError):
        data.quick_quote(q)

    # All-NaN Close raises
    nan_df = _ohlcv_df(["2026-11-20"], [float("nan")])
    tk_nan = MagicMock()
    tk_nan.history.side_effect = [nan_df, pd.DataFrame()]
    tk_nan.fast_info = SimpleNamespace(last_price=None)
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk_nan)
    with pytest.raises(data.StockNotFoundError):
        data.quick_quote(q)


def test_dividends(monkeypatch):
    q = _tw_quote()
    divs = pd.Series(
        [3.5, 4.0, 4.5],
        index=pd.to_datetime(["2024-06-15", "2025-06-15", "2026-06-15"]),
    )
    tk = MagicMock()
    tk.dividends = divs
    tk.history.return_value = _ohlcv_df(["2026-11-20"], [100.0])
    monkeypatch.setattr(data.yf, "Ticker", lambda sym: tk)

    rep = data.dividends(q)
    assert len(rep.years) == 3
    assert rep.latest_amount == 4.5
    assert rep.current_price == 100.0
    assert rep.yield_pct == pytest.approx(4.5)

    # Price lookup fails -> yield_pct is None, still returns report
    tk.history.side_effect = RuntimeError("price err")
    rep_no_px = data.dividends(q)
    assert rep_no_px.current_price is None
    assert rep_no_px.yield_pct is None

    # Empty dividends -> raises
    tk.dividends = pd.Series(dtype=float)
    with pytest.raises(data.DividendDataUnavailable):
        data.dividends(q)

    # Non-positive dividends -> raises
    tk.dividends = pd.Series([0.0], index=pd.to_datetime(["2026-06-15"]))
    with pytest.raises(data.DividendDataUnavailable):
        data.dividends(q)


def test_fx_overview(monkeypatch):
    sample_snap = data.QuickSnapshot(_us_quote(), 32.5, 32.4, 32.4, 32.6, 32.3, 0.0, "2026/11/20")

    def fake_quick_quote(q):
        if q.ticker == aliases.FX_OVERVIEW_PAIRS[0].ticker:
            return sample_snap
        raise data.StockNotFoundError(q.ticker)

    monkeypatch.setattr(data, "quick_quote", fake_quick_quote)
    res = data.fx_overview()
    assert res == [sample_snap]

    monkeypatch.setattr(data, "quick_quote", MagicMock(side_effect=RuntimeError("all fail")))
    with pytest.raises(data.StockNotFoundError):
        data.fx_overview()

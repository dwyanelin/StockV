"""Smoke tests for app.stock.charts: verify each chart type renders a valid PNG."""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.stock import charts
from app.stock.data import AnnualDividend, DividendReport, StockQuote
from app.stock.institutional import InstDay, InstReport

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _tw_quote() -> StockQuote:
    return StockQuote("2330", "2330.TW", "台積電", True, "TWD", "stock")


def test_intraday_chart_renders_valid_png(tmp_path):
    idx = pd.date_range("2026-11-20 09:00", periods=10, freq="5min")
    df = pd.DataFrame(
        {
            "Open": [1000.0 + i for i in range(10)],
            "High": [1002.0 + i for i in range(10)],
            "Low": [999.0 + i for i in range(10)],
            "Close": [1001.0 + i for i in range(10)],
            "Volume": [1000.0] * 10,
        },
        index=idx,
    )
    out = tmp_path / "intraday.png"
    res = charts.intraday_chart(_tw_quote(), df, 1000.0, str(out))
    assert res == str(out)
    assert out.read_bytes().startswith(_PNG_MAGIC)


def test_candlestick_chart_renders_valid_png(tmp_path):
    idx = pd.date_range("2026-10-01", periods=25, freq="B")
    closes = [1000.0 + i * 2 for i in range(25)]
    df = pd.DataFrame(
        {
            "Open": [c - 1 for c in closes],
            "High": [c + 3 for c in closes],
            "Low": [c - 3 for c in closes],
            "Close": closes,
            "Volume": [5000.0] * 25,
        },
        index=idx,
    )
    out = tmp_path / "candle.png"
    res = charts.candlestick_chart(_tw_quote(), df, str(out))
    assert res == str(out)
    assert out.read_bytes().startswith(_PNG_MAGIC)


def test_institutional_chart_renders_valid_png(tmp_path):
    report = InstReport(
        ticker="2330",
        name="台積電",
        days=[
            InstDay(date(2026, 11, 18), 3000.0, 500.0, -200.0, 3300.0),
            InstDay(date(2026, 11, 19), -1000.0, 800.0, 100.0, -100.0),
            InstDay(date(2026, 11, 20), 4000.0, 600.0, 300.0, 4900.0),
        ],
        foreign_holding_pct=73.5,
    )
    out = tmp_path / "inst.png"
    res = charts.institutional_chart(report, str(out))
    assert res == str(out)
    assert out.read_bytes().startswith(_PNG_MAGIC)


def test_dividend_chart_renders_valid_png(tmp_path):
    report = DividendReport(
        quote=_tw_quote(),
        current_price=1000.0,
        years=[AnnualDividend(2024, 14.0), AnnualDividend(2025, 16.0), AnnualDividend(2026, 18.0)],
        latest_ex_date=date(2026, 9, 15),
        latest_amount=18.0,
        avg_5y=16.0,
        yield_pct=1.8,
    )
    out = tmp_path / "div.png"
    res = charts.dividend_chart(report, str(out))
    assert res == str(out)
    assert out.read_bytes().startswith(_PNG_MAGIC)

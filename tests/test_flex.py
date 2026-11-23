"""Unit tests for app.flex (formatting helpers and LINE Flex Message builders)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from app import flex
from app.stock.data import AnnualDividend, DividendReport, QuickSnapshot, StockQuote
from app.stock.fundamentals import FundamentalReport, MarginInfo, MonthlyRevenue, Valuation
from app.stock.institutional import InstDay, InstReport


def _tw_quote() -> StockQuote:
    return StockQuote("2330", "2330.TW", "台積電", True, "TWD", "stock")


def _us_quote() -> StockQuote:
    return StockQuote("AAPL", "AAPL", "Apple", False, "USD", "stock")


def _ohlcv_df(n: int = 5, base: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2026-11-01", periods=n, freq="D")
    closes = [base + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [10_000.0] * n,
        },
        index=idx,
    )


def test_formatting_helpers():
    assert flex._up_down(True) == (flex._UP_TW, flex._DOWN_TW)
    assert flex._up_down(False) == (flex._DOWN_TW, flex._UP_TW)

    assert flex._fmt_px(None) == "--"
    assert flex._fmt_px(0.2145) == "0.2145"
    assert flex._fmt_px(1050.5) == "1,050.50"

    assert flex._fmt_vol(0, True) == "--"
    assert flex._fmt_vol(5_000, True) == "5張"
    assert flex._fmt_vol(25_000_000, True) == "2.50萬張"
    assert flex._fmt_vol(2_500_000, False) == "2.50M"
    assert flex._fmt_vol(2_500, False) == "2.5K"
    assert flex._fmt_vol(500, False) == "500"

    assert flex._fmt_inst(0, 1e8, "億元") == "0.0"
    assert flex._fmt_inst(2.5e8, 1e8, "億元") == "+2.5"
    assert flex._fmt_inst(100, 1000.0, "張") == "0"
    assert flex._fmt_inst(-5000, 1000.0, "張") == "-5"

    assert flex._inst_color(1e8, 1e8, "億元") == flex._UP_TW
    assert flex._inst_color(-1e8, 1e8, "億元") == flex._DOWN_TW
    assert flex._inst_color(0, 1e8, "億元") == flex._MUTED

    assert flex._pct_color(None) == flex._MUTED
    assert flex._pct_color(1.2, True) == flex._UP_TW
    assert flex._pct_color(-1.2, True) == flex._DOWN_TW
    assert flex._pct_color(0.0, True) == flex._MUTED

    assert flex._fmt_pct(None) == "--"
    assert flex._fmt_pct(2.5) == "+2.50%"
    assert flex._fmt_pct(-1.25) == "-1.25%"

    assert flex._fmt_mcap(None) == "--"
    assert flex._fmt_mcap(3.5e12) == "$3.50T"
    assert flex._fmt_mcap(50e9) == "$50.0B"
    assert flex._fmt_mcap(250e6) == "$250M"
    assert flex._fmt_mcap(5000) == "$5,000"


def test_footer_nav_variants():
    tw_nav = flex._footer_nav("2330", "K", True, "stock", kline_tf="D")
    assert len(tw_nav["contents"]) == 2  # timeframe row + primary row

    us_nav = flex._footer_nav("AAPL", "P", False, "stock")
    assert len(us_nav["contents"][0]["contents"]) == 4  # no 法人 button

    mkt_nav = flex._footer_nav("台股", "T", True, "index")
    assert len(mkt_nav["contents"][0]["contents"]) == 3

    crypto_nav = flex._footer_nav("BTC", "#", False, "crypto")
    assert len(crypto_nav["contents"][0]["contents"]) == 3


def test_quote_card_with_and_without_valuation():
    snap = QuickSnapshot(_tw_quote(), 1050.0, 1000.0, 1010.0, 1060.0, 1005.0, 20_000_000.0, "2026/11/20")
    val = Valuation(pe=25.0, pb=6.0, yield_pct=1.8)
    msg = flex.quote_card(snap, val)
    assert "台積電" in msg.alt_text
    assert "+50.00" in msg.alt_text

    # Down / flat without valuation
    snap_flat = QuickSnapshot(_us_quote(), 200.0, 200.0, 200.0, 201.0, 199.0, 1000.0, "2026/11/20")
    msg2 = flex.quote_card(snap_flat, None)
    assert "Apple" in msg2.alt_text


def test_intraday_and_candlestick_cards():
    df = _ohlcv_df(n=65, base=100.0)
    p_msg = flex.intraday_card(_tw_quote(), df, 99.0, "https://example.test/p.png")
    assert "台積電" in p_msg.alt_text

    k_msg = flex.candlestick_card(_tw_quote(), df, "https://example.test/k.png", timeframe="W")
    assert "週K" in k_msg.alt_text

    # Low-priced asset (< 10) formatting path
    low_df = _ohlcv_df(n=2, base=0.5)
    k_low = flex.candlestick_card(
        StockQuote("JPY/TWD", "JPYTWD=X", "日圓/台幣", False, "TWD", "fx"),
        low_df,
        "https://example.test/fx.png",
        timeframe="M",
    )
    assert "月K" in k_low.alt_text


def test_dividend_and_institutional_cards():
    div_rep = DividendReport(
        quote=_tw_quote(),
        current_price=1000.0,
        years=[AnnualDividend(2025, 16.0), AnnualDividend(2026, 18.0)],
        latest_ex_date=date(2026, 9, 15),
        latest_amount=18.0,
        avg_5y=15.0,
        yield_pct=1.8,
    )
    d_msg = flex.dividend_card(div_rep, "https://example.test/d.png")
    assert "股利政策" in d_msg.alt_text

    inst_rep = InstReport(
        ticker="2330",
        name="台積電",
        days=[InstDay(date(2026, 11, 20), 5000.0, 1000.0, -500.0, 5500.0)],
        foreign_holding_pct=73.5,
        market="TWSE",
    )
    t_msg = flex.institutional_card(inst_rep, "https://example.test/t.png")
    assert "上市三大法人" in t_msg.alt_text


def test_fundamental_fx_overview_and_help_cards():
    tw_fund = FundamentalReport(
        quote=_tw_quote(),
        valuation=Valuation(pe=25.0, pb=6.0, yield_pct=1.8),
        revenue=MonthlyRevenue("2026/10", 3000.0, 5.0, 28.0, 31.0),
        margin=MarginInfo(10000.0, 500.0, 400.0, -20.0, 4.0),
    )
    f_tw = flex.fundamental_card(tw_fund)
    assert "基本面與資券摘要" in f_tw.alt_text

    us_fund = FundamentalReport(
        quote=_us_quote(),
        valuation=Valuation(pe=30.0, pb=40.0, yield_pct=0.5),
        market_cap=3e12,
        forward_pe=28.0,
        rev_growth_pct=8.0,
        profit_margin_pct=25.0,
        week52_high=250.0,
        week52_low=170.0,
    )
    f_us = flex.fundamental_card(us_fund)
    assert "Apple" in f_us.alt_text

    fx_snaps = [
        QuickSnapshot(
            StockQuote("USD/TWD", "TWD=X", "美元/台幣", False, "TWD", "fx"),
            32.5, 32.4, 32.4, 32.6, 32.3, 0.0, "2026/11/20",
        )
    ]
    fx_msg = flex.fx_overview_card(fx_snaps)
    assert "匯率" in fx_msg.alt_text

    h_msg = flex.help_card()
    assert "指令說明" in h_msg.alt_text
    assert h_msg.quick_reply is not None

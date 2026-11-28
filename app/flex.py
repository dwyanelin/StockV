"""Dark-mode LINE Flex Message builders for StockV.

Supports cohesive Flex cards for:
- # (Quick quote)
- P (Intraday chart) & P外匯 (FX overview)
- K / KW / KM (Daily / Weekly / Monthly candlestick with 1-tap timeframe switcher)
- D (Dividend policy chart)
- T (Institutional net buy/sell: TWSE listed, TPEx OTC, and TWSE market)
- F (Fundamentals: valuation, monthly revenue, margin trading / US metrics)
- help (Command overview + Quick Reply chips)
"""
from __future__ import annotations

from urllib.parse import urlencode

import pandas as pd
from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
    MessageAction,
    QuickReply,
    QuickReplyItem,
)

from app.stock.data import DividendReport, QuickSnapshot, StockQuote, intraday_total_volume
from app.stock.fundamentals import FundamentalReport, Valuation
from app.stock.institutional import InstReport

_BG = "#131926"
_SURFACE = "#1c2436"
_BORDER = "#263147"
_TEXT = "#f1f5f9"
_MUTED = "#94a3b8"
_UP_TW = "#f6465d"
_DOWN_TW = "#2ebd85"
_ACCENT = "#3b82f6"


def _up_down(is_taiwan: bool) -> tuple[str, str]:
    return (_UP_TW, _DOWN_TW) if is_taiwan else (_DOWN_TW, _UP_TW)


def _fmt_px(val: float | None) -> str:
    if val is None:
        return "--"
    if abs(val) < 1 and val != 0:
        return f"{val:.4f}"
    return f"{val:,.2f}"


def _fmt_vol(shares: float, is_taiwan: bool) -> str:
    if shares <= 0:
        return "--"
    if is_taiwan:
        lots = shares / 1000.0
        if lots >= 10000:
            return f"{lots / 10000:.2f}萬張"
        return f"{lots:,.0f}張"
    if shares >= 1_000_000:
        return f"{shares / 1_000_000:.2f}M"
    if shares >= 1_000:
        return f"{shares / 1_000:.1f}K"
    return f"{shares:,.0f}"


def _fmt_inst(val: float, scale: float, unit: str) -> str:
    v = val / (scale or 1000.0)
    if unit == "億元":
        return "0.0" if abs(v) < 0.05 else f"{v:+,.1f}"
    return "0" if abs(v) < 0.5 else f"{v:+,.0f}"


def _inst_color(val: float, scale: float, unit: str) -> str:
    v = val / (scale or 1000.0)
    thresh = 0.05 if unit == "億元" else 0.5
    if v >= thresh:
        return _UP_TW
    if v <= -thresh:
        return _DOWN_TW
    return _MUTED


def _pct_color(val: float | None, is_taiwan: bool = True) -> str:
    if val is None:
        return _MUTED
    up, down = _up_down(is_taiwan)
    if val > 0:
        return up
    if val < 0:
        return down
    return _MUTED


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "--"
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def _fmt_mcap(val: float | None, is_taiwan: bool = False) -> str:
    if val is None:
        return "--"
    if is_taiwan:
        if val >= 1e12:
            return f"{val / 1e12:.2f}兆"
        if val >= 1e8:
            return f"{val / 1e8:,.0f}億"
        return f"{val / 1e4:,.0f}萬"
    if val >= 1e12:
        return f"${val / 1e12:.2f}T"
    if val >= 1e9:
        return f"${val / 1e9:.1f}B"
    if val >= 1e6:
        return f"${val / 1e6:.0f}M"
    return f"${val:,.0f}"


def _roe_color(roe_pct: float | None) -> str:
    if roe_pct is None:
        return _MUTED
    if roe_pct >= 15.0:
        return "#2ebd85"
    if roe_pct < 0.0:
        return "#f6465d"
    return _TEXT


def _fmt_ev_ebitda(report: FundamentalReport) -> tuple[str, str]:
    if report.is_financial:
        return "金融不適用", _MUTED
    if report.ev_ebitda is None:
        return "--", _MUTED
    return f"{report.ev_ebitda:.2f}x", _TEXT


def _metric_cell(label: str, value: str, value_color: str = _TEXT) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "flex": 1,
        "spacing": "xs",
        "contents": [
            {"type": "text", "text": label, "size": "xxs", "color": _MUTED},
            {
                "type": "text",
                "text": value,
                "size": "sm",
                "weight": "bold",
                "color": value_color,
                "adjustMode": "shrink-to-fit",
            },
        ],
    }


def _header_row(name: str, ticker: str, badge: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "alignItems": "center",
        "contents": [
            {
                "type": "text",
                "text": f"{name} · {ticker}",
                "weight": "bold",
                "size": "md",
                "color": _TEXT,
                "flex": 1,
                "wrap": False,
                "adjustMode": "shrink-to-fit",
            },
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": _SURFACE,
                "cornerRadius": "sm",
                "paddingStart": "8px",
                "paddingEnd": "8px",
                "paddingTop": "3px",
                "paddingBottom": "3px",
                "flex": 0,
                "contents": [
                    {"type": "text", "text": badge, "size": "xxs", "color": _MUTED, "weight": "bold"}
                ],
            },
        ],
    }


def _pill_btn(label: str, cmd: str, query: str, highlighted: bool = False) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "flex": 1,
        "backgroundColor": _ACCENT if highlighted else _SURFACE,
        "cornerRadius": "md",
        "paddingTop": "7px",
        "paddingBottom": "7px",
        "alignItems": "center",
        "action": {
            "type": "postback",
            "label": label,
            "data": urlencode({"cmd": cmd, "q": query}),
        },
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "xs",
                "weight": "bold",
                "color": "#ffffff" if highlighted else _TEXT,
            }
        ],
    }


def _footer_nav(
    ticker: str,
    active: str,
    is_taiwan: bool,
    asset_type: str = "stock",
    kline_tf: str | None = None,
) -> dict:
    rows: list[dict] = []

    # Optional timeframe switcher row for candlestick cards
    if kline_tf is not None:
        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _pill_btn("日K", "K", ticker, highlighted=(kline_tf == "D")),
                    _pill_btn("週K", "KW", ticker, highlighted=(kline_tf == "W")),
                    _pill_btn("月K", "KM", ticker, highlighted=(kline_tf == "M")),
                ],
            }
        )

    # Primary view switcher row
    if asset_type == "stock":
        if is_taiwan:
            btns = [
                _pill_btn("走勢" if active != "P" else "刷新", "P", ticker, highlighted=(active == "P")),
                _pill_btn("K線" if active != "K" else "刷新", "K", ticker, highlighted=(active == "K")),
                _pill_btn("法人" if active != "T" else "刷新", "T", ticker, highlighted=(active == "T")),
                _pill_btn("股利" if active != "D" else "刷新", "D", ticker, highlighted=(active == "D")),
                _pill_btn("基本" if active != "F" else "刷新", "F", ticker, highlighted=(active == "F")),
            ]
        else:
            btns = [
                _pill_btn("走勢" if active != "P" else "刷新", "P", ticker, highlighted=(active == "P")),
                _pill_btn("K線" if active != "K" else "刷新", "K", ticker, highlighted=(active == "K")),
                _pill_btn("股利" if active != "D" else "刷新", "D", ticker, highlighted=(active == "D")),
                _pill_btn("基本" if active != "F" else "刷新", "F", ticker, highlighted=(active == "F")),
            ]
    elif ticker == "台股":
        btns = [
            _pill_btn("走勢" if active != "P" else "刷新", "P", "台股", highlighted=(active == "P")),
            _pill_btn("K線" if active != "K" else "刷新", "K", "台股", highlighted=(active == "K")),
            _pill_btn("法人" if active != "T" else "刷新", "T", "台股", highlighted=(active == "T")),
        ]
    else:
        btns = [
            _pill_btn("走勢" if active != "P" else "刷新", "P", ticker, highlighted=(active == "P")),
            _pill_btn("K線" if active != "K" else "刷新", "K", ticker, highlighted=(active == "K")),
            _pill_btn("報價" if active != "#" else "刷新", "#", ticker, highlighted=(active == "#")),
        ]

    rows.append({"type": "box", "layout": "horizontal", "spacing": "xs", "contents": btns})

    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "paddingAll": "12px",
        "paddingTop": "4px",
        "backgroundColor": _BG,
        "contents": rows,
    }


def _wrap_bubble(
    body_contents: list[dict],
    footer: dict,
    alt_text: str,
    chart_url: str | None = None,
    size: str = "mega",
) -> FlexMessage:
    bubble: dict = {
        "type": "bubble",
        "size": size,
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "14px",
            "backgroundColor": _BG,
            "contents": body_contents,
        },
        "footer": footer,
    }
    if chart_url:
        bubble["hero"] = {
            "type": "image",
            "url": chart_url,
            "size": "full",
            "aspectRatio": "16:10",
            "aspectMode": "cover",
            "backgroundColor": "#0f141e",
            "action": {"type": "uri", "label": "查看原圖", "uri": chart_url},
        }
    return FlexMessage(alt_text=alt_text[:400], contents=FlexContainer.from_dict(bubble))


def quote_card(snap: QuickSnapshot, valuation: Valuation | None = None) -> FlexMessage:
    """Fast pure-data quote card (# command) with zero chart render latency."""
    q = snap.quote
    ref = snap.prev_close if snap.prev_close else (snap.open_price or snap.price)
    chg = snap.price - ref
    pct = (chg / ref * 100.0) if ref else 0.0
    up, down = _up_down(q.is_taiwan)
    color = up if chg >= 0 else down
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "─")
    sign = "+" if chg > 0 else ""

    type_labels = {
        "stock": "即時報價",
        "index": "指數報價",
        "futures": "期貨報價",
        "crypto": "加密貨幣",
        "fx": "外匯報價",
        "commodity": "商品期貨",
    }
    badge = type_labels.get(q.asset_type, "即時報價")

    body: list[dict] = [
        _header_row(q.name, q.ticker, badge),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": _fmt_px(snap.price), "size": "xxl", "weight": "bold", "color": color, "flex": 0},
                {"type": "text", "text": f"{arrow} {sign}{_fmt_px(chg)} ({sign}{pct:.2f}%)", "size": "sm", "weight": "bold", "color": color, "flex": 0},
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell("開盤", _fmt_px(snap.open_price)),
                _metric_cell("最高", _fmt_px(snap.high_price), up),
                _metric_cell("最低", _fmt_px(snap.low_price), down),
                _metric_cell("成交量", _fmt_vol(snap.volume, q.is_taiwan)),
            ],
        },
    ]
    if valuation is not None and any((valuation.pe, valuation.pb, valuation.yield_pct)):
        body.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _metric_cell("昨收", _fmt_px(snap.prev_close)),
                    _metric_cell("本益比", f"{valuation.pe:.1f}" if valuation.pe else "--"),
                    _metric_cell("淨值比", f"{valuation.pb:.2f}" if valuation.pb else "--"),
                    _metric_cell("殖利率", f"{valuation.yield_pct:.2f}%" if valuation.yield_pct else "--", "#2ebd85"),
                ],
            }
        )
    body.append({"type": "text", "text": f"資料日期 {snap.as_of} · 點下方按鈕切換圖表", "size": "xxs", "color": _MUTED})

    footer = _footer_nav(q.ticker, "#", q.is_taiwan, q.asset_type)
    alt = f"{q.name} ({q.ticker}) {_fmt_px(snap.price)} {sign}{_fmt_px(chg)} ({sign}{pct:.2f}%)"
    return _wrap_bubble(body, footer, alt, chart_url=None, size="kilo")


def intraday_card(
    quote: StockQuote, df: pd.DataFrame, prev_close: float | None, chart_url: str
) -> FlexMessage:
    close = df["Close"].dropna()
    last = float(close.iloc[-1])
    open_px = float(df["Open"].dropna().iloc[0]) if "Open" in df.columns and not df["Open"].dropna().empty else float(close.iloc[0])
    high_px = float(df["High"].dropna().max()) if "High" in df.columns and not df["High"].dropna().empty else float(close.max())
    low_px = float(df["Low"].dropna().min()) if "Low" in df.columns and not df["Low"].dropna().empty else float(close.min())
    total_vol = intraday_total_volume(df)

    ref = prev_close if prev_close else open_px
    chg = last - ref
    pct = (chg / ref * 100) if ref else 0.0
    up, down = _up_down(quote.is_taiwan)
    color = up if chg >= 0 else down
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "─")
    sign = "+" if chg > 0 else ""
    ts = close.index[-1].strftime("%Y/%m/%d %H:%M")

    body = [
        _header_row(quote.name, quote.ticker, "當日走勢"),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": _fmt_px(last), "size": "xxl", "weight": "bold", "color": color, "flex": 0},
                {"type": "text", "text": f"{arrow} {sign}{_fmt_px(chg)} ({sign}{pct:.2f}%)", "size": "sm", "weight": "bold", "color": color, "flex": 0},
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell("開盤", _fmt_px(open_px)),
                _metric_cell("最高", _fmt_px(high_px), up),
                _metric_cell("最低", _fmt_px(low_px), down),
                _metric_cell("成交量", _fmt_vol(total_vol, quote.is_taiwan)),
            ],
        },
        {"type": "text", "text": f"更新時間 {ts} · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    footer = _footer_nav(quote.ticker, "P", quote.is_taiwan, quote.asset_type)
    alt = f"{quote.name} ({quote.ticker}) {_fmt_px(last)} {sign}{_fmt_px(chg)} ({sign}{pct:.2f}%)"
    return _wrap_bubble(body, footer, alt, chart_url=chart_url)


def candlestick_card(
    quote: StockQuote, df: pd.DataFrame, chart_url: str, timeframe: str = "D"
) -> FlexMessage:
    tf = timeframe.upper()
    badges = {"D": "日K 近6月", "W": "週K 近2年", "M": "月K 近5年"}
    range_labels = {"D": "6月區間", "W": "2年區間", "M": "5年區間"}

    last = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else float(df["Open"].iloc[-1])
    chg = last - prev
    pct = (chg / prev * 100) if prev else 0.0
    up, down = _up_down(quote.is_taiwan)
    color = up if chg >= 0 else down
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "─")
    sign = "+" if chg > 0 else ""

    close_s = df["Close"]
    ma5 = close_s.rolling(5).mean().iloc[-1] if len(close_s) >= 5 else None
    ma20 = close_s.rolling(20).mean().iloc[-1] if len(close_s) >= 20 else None
    ma60 = close_s.rolling(60).mean().iloc[-1] if len(close_s) >= 60 else None
    hi_r = float(df["High"].max())
    lo_r = float(df["Low"].min())
    day_str = df.index[-1].strftime("%Y/%m/%d")

    def _ma(v) -> str:
        return _fmt_px(float(v)) if v is not None and pd.notna(v) else "--"

    body = [
        _header_row(quote.name, quote.ticker, badges.get(tf, "日K 近6月")),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": _fmt_px(last), "size": "xxl", "weight": "bold", "color": color, "flex": 0},
                {"type": "text", "text": f"{arrow} {sign}{_fmt_px(chg)} ({sign}{pct:.2f}%)", "size": "sm", "weight": "bold", "color": color, "flex": 0},
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell("MA5", _ma(ma5), "#f59e0b"),
                _metric_cell("MA20", _ma(ma20), "#3b82f6"),
                _metric_cell("MA60", _ma(ma60), "#a78bfa"),
                _metric_cell(range_labels.get(tf, "區間"), f"{lo_r:,.0f}–{hi_r:,.0f}" if hi_r >= 10 else f"{lo_r:.3f}–{hi_r:.3f}"),
            ],
        },
        {"type": "text", "text": f"資料截至 {day_str} · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    footer = _footer_nav(quote.ticker, "K", quote.is_taiwan, quote.asset_type, kline_tf=tf)
    alt = f"{quote.name} ({quote.ticker}) {badges.get(tf, '日K')} 收 {_fmt_px(last)} ({sign}{pct:.2f}%)"
    return _wrap_bubble(body, footer, alt, chart_url=chart_url)


def dividend_card(report: DividendReport, chart_url: str) -> FlexMessage:
    q = report.quote
    unit = "元" if q.is_taiwan else q.currency
    yield_str = f"{report.yield_pct:.2f}%" if report.yield_pct is not None else "--"
    ex_str = f"{report.latest_ex_date:%Y/%m/%d}" if report.latest_ex_date else "--"

    body = [
        _header_row(q.name, q.ticker, "股利政策"),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": f"殖利率 {yield_str}", "size": "xl", "weight": "bold", "color": "#2ebd85", "flex": 0},
                {"type": "text", "text": f"現價 {_fmt_px(report.current_price)}", "size": "xs", "color": _MUTED, "flex": 0},
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell(f"最新年度 ({unit})", f"{report.latest_amount:.2f}", "#f59e0b"),
                _metric_cell(f"近5年均 ({unit})", f"{report.avg_5y:.2f}"),
                _metric_cell("最新除息日", ex_str),
            ],
        },
        {"type": "text", "text": f"統計近 {len(report.years)} 年現金股利 · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    footer = _footer_nav(q.ticker, "D", q.is_taiwan, q.asset_type)
    alt = f"{q.name} ({q.ticker}) 股利政策：最新 {report.latest_amount:.2f} {unit}，殖利率 {yield_str}"
    return _wrap_bubble(body, footer, alt, chart_url=chart_url)


def institutional_card(report: InstReport, chart_url: str) -> FlexMessage:
    latest = report.days[-1]
    recent5 = report.days[-5:]
    sum5 = sum(d.total for d in recent5)
    scale, unit = report.scale, report.unit
    badge = {"TWSE": "上市三大法人", "TPEX": "上櫃三大法人", "MARKET": "大盤三大法人"}.get(report.market, "三大法人")
    sub = f"外資持股 {report.foreign_holding_pct:.2f}%" if report.foreign_holding_pct is not None else f"單位：{unit}"

    body = [
        _header_row(report.name, report.ticker, badge),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": f"合計 {_fmt_inst(latest.total, scale, unit)} {unit}",
                    "size": "xl",
                    "weight": "bold",
                    "color": _inst_color(latest.total, scale, unit),
                    "flex": 0,
                },
                {"type": "text", "text": sub, "size": "xs", "color": _MUTED, "flex": 0},
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell(f"外資 ({unit})", _fmt_inst(latest.foreign, scale, unit), _inst_color(latest.foreign, scale, unit)),
                _metric_cell(f"投信 ({unit})", _fmt_inst(latest.trust, scale, unit), _inst_color(latest.trust, scale, unit)),
                _metric_cell(f"自營商 ({unit})", _fmt_inst(latest.dealer, scale, unit), _inst_color(latest.dealer, scale, unit)),
                _metric_cell("近5日合計", _fmt_inst(sum5, scale, unit), _inst_color(sum5, scale, unit)),
            ],
        },
        {"type": "text", "text": f"交易日 {latest.day:%Y/%m/%d} · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    asset_type = "index" if report.market == "MARKET" else "stock"
    footer = _footer_nav(report.ticker, "T", True, asset_type)
    alt = f"{report.name} ({report.ticker}) {badge} {latest.day:%Y/%m/%d} 合計 {_fmt_inst(latest.total, scale, unit)} {unit}"
    return _wrap_bubble(body, footer, alt, chart_url=chart_url)


def fundamental_card(report: FundamentalReport) -> FlexMessage:
    """Fundamentals card: valuation + monthly revenue + margin (TW) or US metrics."""
    q = report.quote
    val = report.valuation
    body: list[dict] = [_header_row(q.name, q.ticker, "基本面 / 資券" if q.is_taiwan else "美股基本面")]

    has_val_section = (
        val is not None
        or report.roe_pct is not None
        or report.ev_ebitda is not None
        or report.market_cap is not None
        or report.is_financial
    )
    if has_val_section:
        pe_str = f"{val.pe:.2f}" if (val and val.pe) else "--"
        pb_str = f"{val.pb:.2f}" if (val and val.pb) else "--"
        yld_str = f"{val.yield_pct:.2f}%" if (val and val.yield_pct) else "--"
        roe_str = f"{report.roe_pct:.2f}%" if report.roe_pct is not None else "--"
        ev_str, ev_col = _fmt_ev_ebitda(report)
        mcap_str = _fmt_mcap(report.market_cap, is_taiwan=q.is_taiwan)

        body.append({"type": "separator", "color": _BORDER})
        body.append({"type": "text", "text": "估值與獲利指標", "size": "xs", "weight": "bold", "color": _ACCENT})
        body.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _metric_cell("本益比 PE", pe_str),
                    _metric_cell("淨值比 PB", pb_str),
                    _metric_cell("殖利率", yld_str, "#2ebd85" if (val and val.yield_pct) else _MUTED),
                ],
            }
        )
        body.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _metric_cell("ROE (TTM)", roe_str, _roe_color(report.roe_pct)),
                    _metric_cell("EV/EBITDA", ev_str, ev_col),
                    _metric_cell("總市值", mcap_str),
                ],
            }
        )

    if report.revenue is not None:
        rev = report.revenue
        body.append({"type": "separator", "color": _BORDER})
        body.append({"type": "text", "text": f"月營收（{rev.period}）", "size": "xs", "weight": "bold", "color": _ACCENT})
        body.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _metric_cell("單月營收", f"{rev.revenue_yi:,.2f}億" if rev.revenue_yi is not None else "--"),
                    _metric_cell("月增 MoM", _fmt_pct(rev.mom_pct), _pct_color(rev.mom_pct, True)),
                    _metric_cell("年增 YoY", _fmt_pct(rev.yoy_pct), _pct_color(rev.yoy_pct, True)),
                    _metric_cell("累計 YoY", _fmt_pct(rev.cum_yoy_pct), _pct_color(rev.cum_yoy_pct, True)),
                ],
            }
        )

    if report.margin is not None:
        m = report.margin
        m_chg = f"{m.margin_change:+,.0f}" if m.margin_change is not None else "--"
        body.append({"type": "separator", "color": _BORDER})
        body.append({"type": "text", "text": "融資融券（張）", "size": "xs", "weight": "bold", "color": _ACCENT})
        body.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _metric_cell("融資餘額", f"{m.margin_balance:,.0f}" if m.margin_balance is not None else "--"),
                    _metric_cell("融資增減", m_chg, _pct_color(m.margin_change, True)),
                    _metric_cell("融券餘額", f"{m.short_balance:,.0f}" if m.short_balance is not None else "--"),
                    _metric_cell("券資比", f"{m.short_ratio_pct:.1f}%" if m.short_ratio_pct is not None else "--"),
                ],
            }
        )

    if not q.is_taiwan:
        body.append({"type": "separator", "color": _BORDER})
        body.append({"type": "text", "text": "營運與區間", "size": "xs", "weight": "bold", "color": _ACCENT})
        w52 = f"{report.week52_low:,.1f}–{report.week52_high:,.1f}" if (report.week52_low and report.week52_high) else "--"
        body.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    _metric_cell("預估 PE", f"{report.forward_pe:.1f}" if report.forward_pe else "--"),
                    _metric_cell("營收成長", _fmt_pct(report.rev_growth_pct), _pct_color(report.rev_growth_pct, False)),
                    _metric_cell("淨利率", _fmt_pct(report.profit_margin_pct)),
                ],
            }
        )
        body.append({"type": "text", "text": f"52 週區間：{w52}", "size": "xxs", "color": _MUTED})

    footer = _footer_nav(q.ticker, "F", q.is_taiwan, q.asset_type)
    alt = f"{q.name} ({q.ticker}) 基本面與資券摘要"
    return _wrap_bubble(body, footer, alt, chart_url=None, size="mega")


def fx_overview_card(snapshots: list[QuickSnapshot]) -> FlexMessage:
    """International FX overview table for P外匯 / #外匯."""
    rows: list[dict] = [
        _header_row("國際主要匯率", "FX", "即時匯率"),
        {"type": "separator", "color": _BORDER},
    ]
    for snap in snapshots:
        ref = snap.prev_close or snap.price
        chg_pct = ((snap.price - ref) / ref * 100.0) if ref else 0.0
        col = _pct_color(chg_pct, is_taiwan=False)
        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "alignItems": "center",
                "contents": [
                    {"type": "text", "text": snap.quote.ticker, "size": "sm", "weight": "bold", "color": _TEXT, "flex": 3},
                    {"type": "text", "text": _fmt_px(snap.price), "size": "sm", "weight": "bold", "color": _TEXT, "align": "end", "flex": 3},
                    {"type": "text", "text": _fmt_pct(chg_pct), "size": "xs", "weight": "bold", "color": col, "align": "end", "flex": 2},
                ],
            }
        )
    rows.append({"type": "separator", "color": _BORDER})
    rows.append({"type": "text", "text": "輸入 PUSD/TWD、P日圓 或 #BTC 可看單一商品走勢", "size": "xxs", "color": _MUTED, "wrap": True})

    footer = {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "paddingAll": "12px",
        "paddingTop": "4px",
        "backgroundColor": _BG,
        "contents": [
            _pill_btn("美元走勢", "P", "USD/TWD"),
            _pill_btn("日圓走勢", "P", "JPY/TWD"),
            _pill_btn("刷新", "P", "外匯", highlighted=True),
        ],
    }
    return _wrap_bubble(rows, footer, "國際主要匯率即時總覽", chart_url=None, size="kilo")


def help_card() -> FlexMessage:
    def _cmd_row(cmd: str, desc: str, example: str) -> dict:
        return {
            "type": "box",
            "layout": "horizontal",
            "spacing": "md",
            "alignItems": "center",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "width": "52px",
                    "backgroundColor": _SURFACE,
                    "cornerRadius": "md",
                    "paddingTop": "6px",
                    "paddingBottom": "6px",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": cmd, "size": "xs", "weight": "bold", "color": _ACCENT}
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "flex": 1,
                    "contents": [
                        {"type": "text", "text": desc, "size": "sm", "weight": "bold", "color": _TEXT},
                        {"type": "text", "text": example, "size": "xs", "color": _MUTED},
                    ],
                },
            ],
        }

    bubble = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "backgroundColor": _BG,
            "contents": [
                {"type": "text", "text": "StockV 指令大全", "weight": "bold", "size": "lg", "color": _TEXT},
                {"type": "text", "text": "支援台股（代號/中文名）、美股、大盤、匯率與加密貨幣", "size": "xs", "color": _MUTED, "wrap": True},
                {"type": "separator", "color": _BORDER},
                _cmd_row("#", "極速文字報價卡", "例：#2330、#台積電、#BTC"),
                _cmd_row("P", "當日即時走勢圖", "例：P玉晶光、P台股、P外匯"),
                _cmd_row("K", "日K / 週K (KW) / 月K (KM)", "例：K2330、KW台積電、KMAAPL"),
                _cmd_row("T", "三大法人（上市/上櫃/大盤）", "例：T2330、T元太、T台股"),
                _cmd_row("D", "歷年股利政策與殖利率", "例：D2330、D0050、DAAPL"),
                _cmd_row("F", "基本面（估值/月營收/資券）", "例：F2330、F玉晶光、FAAPL"),
                {"type": "separator", "color": _BORDER},
                {"type": "text", "text": "點圖表看原圖，點卡片下方按鈕可一鍵切換所有視圖。", "size": "xxs", "color": _MUTED, "wrap": True},
            ],
        },
    }
    quick_reply = QuickReply(
        items=[
            QuickReplyItem(action=MessageAction(label="#台積電", text="#台積電")),
            QuickReplyItem(action=MessageAction(label="P台股", text="P台股")),
            QuickReplyItem(action=MessageAction(label="T台股", text="T台股")),
            QuickReplyItem(action=MessageAction(label="D2330", text="D2330")),
            QuickReplyItem(action=MessageAction(label="F2330", text="F2330")),
            QuickReplyItem(action=MessageAction(label="P外匯", text="P外匯")),
            QuickReplyItem(action=MessageAction(label="PBTC", text="PBTC")),
        ]
    )
    return FlexMessage(
        alt_text="StockV 指令說明：# / P / K / KW / KM / T / D / F",
        contents=FlexContainer.from_dict(bubble),
        quick_reply=quick_reply,
    )

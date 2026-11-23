"""Dark-mode LINE Flex Message builders for StockV.

Each P/K/T command replies with a single cohesive Flex card containing:
- Full-bleed chart hero (tap to open full-resolution PNG)
- Key numeric metrics aligned cleanly without awkward line breaks
- One-tap Postback buttons to switch between 走勢 / 日K / 法人 / 刷新
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

from app.stock.data import StockQuote
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


def _fmt_vol(shares: float, is_taiwan: bool) -> str:
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


def _fmt_lots(shares: float) -> str:
    lots = shares / 1000.0
    if abs(lots) < 0.5:
        return "0"
    return f"{lots:+,.0f}"


def _lots_color(shares: float) -> str:
    lots = shares / 1000.0
    if lots >= 0.5:
        return _UP_TW
    if lots <= -0.5:
        return _DOWN_TW
    return _MUTED


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


def _footer_buttons(ticker: str, active: str, is_taiwan: bool) -> dict:
    items: list[tuple[str, str]] = [
        ("走勢" if active != "P" else "刷新走勢", "P"),
        ("日K" if active != "K" else "刷新日K", "K"),
    ]
    if is_taiwan:
        items.append(("法人" if active != "T" else "刷新法人", "T"))

    buttons = []
    for label, cmd in items:
        is_active = cmd == active
        buttons.append(
            {
                "type": "box",
                "layout": "vertical",
                "flex": 1,
                "backgroundColor": _ACCENT if is_active else _SURFACE,
                "cornerRadius": "md",
                "paddingTop": "8px",
                "paddingBottom": "8px",
                "alignItems": "center",
                "action": {
                    "type": "postback",
                    "label": label,
                    "data": urlencode({"cmd": cmd, "q": ticker}),
                },
                "contents": [
                    {
                        "type": "text",
                        "text": label,
                        "size": "xs",
                        "weight": "bold",
                        "color": "#ffffff" if is_active else _TEXT,
                    }
                ],
            }
        )

    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "paddingAll": "12px",
        "paddingTop": "4px",
        "backgroundColor": _BG,
        "contents": buttons,
    }


def _wrap_bubble(
    chart_url: str,
    body_contents: list[dict],
    ticker: str,
    active: str,
    is_taiwan: bool,
    alt_text: str,
) -> FlexMessage:
    bubble = {
        "type": "bubble",
        "size": "mega",
        "hero": {
            "type": "image",
            "url": chart_url,
            "size": "full",
            "aspectRatio": "16:10",
            "aspectMode": "cover",
            "backgroundColor": "#0f141e",
            "action": {"type": "uri", "label": "查看原圖", "uri": chart_url},
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "14px",
            "backgroundColor": _BG,
            "contents": body_contents,
        },
        "footer": _footer_buttons(ticker, active, is_taiwan),
    }
    return FlexMessage(alt_text=alt_text[:400], contents=FlexContainer.from_dict(bubble))


def intraday_card(
    quote: StockQuote, df: pd.DataFrame, prev_close: float | None, chart_url: str
) -> FlexMessage:
    close = df["Close"].dropna()
    last = float(close.iloc[-1])
    open_px = float(df["Open"].dropna().iloc[0]) if "Open" in df.columns and not df["Open"].dropna().empty else float(close.iloc[0])
    high_px = float(df["High"].dropna().max()) if "High" in df.columns and not df["High"].dropna().empty else float(close.max())
    low_px = float(df["Low"].dropna().min()) if "Low" in df.columns and not df["Low"].dropna().empty else float(close.min())
    total_vol = float(df["Volume"].dropna().sum()) if "Volume" in df.columns else 0.0

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
                {"type": "text", "text": f"{last:,.2f}", "size": "xxl", "weight": "bold", "color": color, "flex": 0},
                {"type": "text", "text": f"{arrow} {sign}{chg:,.2f} ({sign}{pct:.2f}%)", "size": "sm", "weight": "bold", "color": color, "flex": 0},
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell("開盤", f"{open_px:,.2f}"),
                _metric_cell("最高", f"{high_px:,.2f}", up),
                _metric_cell("最低", f"{low_px:,.2f}", down),
                _metric_cell("成交量", _fmt_vol(total_vol, quote.is_taiwan) if total_vol > 0 else "--"),
            ],
        },
        {"type": "text", "text": f"更新時間 {ts} · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    alt = f"{quote.name} ({quote.ticker}) {last:,.2f} {sign}{chg:,.2f} ({sign}{pct:.2f}%)"
    return _wrap_bubble(chart_url, body, quote.ticker, "P", quote.is_taiwan, alt)


def candlestick_card(quote: StockQuote, df: pd.DataFrame, chart_url: str) -> FlexMessage:
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
    hi_6m = float(df["High"].max())
    lo_6m = float(df["Low"].min())
    day_str = df.index[-1].strftime("%Y/%m/%d")

    def _ma(v) -> str:
        return f"{float(v):,.1f}" if v is not None and pd.notna(v) else "--"

    body = [
        _header_row(quote.name, quote.ticker, "日K 近6月"),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": f"{last:,.2f}", "size": "xxl", "weight": "bold", "color": color, "flex": 0},
                {"type": "text", "text": f"{arrow} {sign}{chg:,.2f} ({sign}{pct:.2f}%)", "size": "sm", "weight": "bold", "color": color, "flex": 0},
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
                _metric_cell("6月區間", f"{lo_6m:,.0f}–{hi_6m:,.0f}"),
            ],
        },
        {"type": "text", "text": f"資料截至 {day_str} · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    alt = f"{quote.name} ({quote.ticker}) 日K 收 {last:,.2f} ({sign}{pct:.2f}%)"
    return _wrap_bubble(chart_url, body, quote.ticker, "K", quote.is_taiwan, alt)


def institutional_card(report: InstReport, chart_url: str) -> FlexMessage:
    latest = report.days[-1]
    recent5 = report.days[-5:]
    sum5 = sum(d.total for d in recent5)
    holding = f"{report.foreign_holding_pct:.2f}%" if report.foreign_holding_pct is not None else "--"

    body = [
        _header_row(report.name, report.ticker, "三大法人"),
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": f"合計 {_fmt_lots(latest.total)} 張",
                    "size": "xl",
                    "weight": "bold",
                    "color": _lots_color(latest.total),
                    "flex": 0,
                },
                {
                    "type": "text",
                    "text": f"外資持股 {holding}",
                    "size": "xs",
                    "color": _MUTED,
                    "flex": 0,
                },
            ],
        },
        {"type": "separator", "color": _BORDER},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _metric_cell("外資 (張)", _fmt_lots(latest.foreign), _lots_color(latest.foreign)),
                _metric_cell("投信 (張)", _fmt_lots(latest.trust), _lots_color(latest.trust)),
                _metric_cell("自營商 (張)", _fmt_lots(latest.dealer), _lots_color(latest.dealer)),
                _metric_cell("近5日合計", _fmt_lots(sum5), _lots_color(sum5)),
            ],
        },
        {"type": "text", "text": f"交易日 {latest.day:%Y/%m/%d} · 點擊圖表看原圖", "size": "xxs", "color": _MUTED},
    ]
    alt = f"{report.name} ({report.ticker}) 三大法人 {latest.day:%Y/%m/%d} 合計 {_fmt_lots(latest.total)} 張"
    return _wrap_bubble(chart_url, body, report.ticker, "T", True, alt)


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
                    "width": "44px",
                    "backgroundColor": _SURFACE,
                    "cornerRadius": "md",
                    "paddingTop": "6px",
                    "paddingBottom": "6px",
                    "alignItems": "center",
                    "contents": [
                        {"type": "text", "text": cmd, "size": "sm", "weight": "bold", "color": _ACCENT}
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
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "backgroundColor": _BG,
            "contents": [
                {"type": "text", "text": "StockV 指令說明", "weight": "bold", "size": "lg", "color": _TEXT},
                {"type": "text", "text": "台股支援代號或中文股名，美股輸入英文代號", "size": "xs", "color": _MUTED, "wrap": True},
                {"type": "separator", "color": _BORDER},
                _cmd_row("P", "當日即時走勢圖", "例：P2330、P玉晶光、PAAPL"),
                _cmd_row("K", "日K線圖（近6個月 + MA5/20/60）", "例：K2330、K玉晶光、KTSLA"),
                _cmd_row("T", "三大法人買賣超 + 外資持股比", "例：T2330、T台積電（台股上市）"),
                {"type": "separator", "color": _BORDER},
                {"type": "text", "text": "小技巧：點圖表看原圖，點卡片下方按鈕可一鍵切換視圖。", "size": "xxs", "color": _MUTED, "wrap": True},
            ],
        },
    }
    quick_reply = QuickReply(
        items=[
            QuickReplyItem(action=MessageAction(label="P台積電", text="P台積電")),
            QuickReplyItem(action=MessageAction(label="K台積電", text="K台積電")),
            QuickReplyItem(action=MessageAction(label="T台積電", text="T台積電")),
            QuickReplyItem(action=MessageAction(label="PAAPL", text="PAAPL")),
        ]
    )
    return FlexMessage(
        alt_text="StockV 使用說明：P/K/T + 股票代號或中文名稱",
        contents=FlexContainer.from_dict(bubble),
        quick_reply=quick_reply,
    )

"""StockV — LINE stock & multi-asset analytics bot.

Commands (case-insensitive):
  #{ticker|name}   fast quote card                 e.g. #2330, #台積電, #BTC
  P{ticker|name}   intraday price chart            e.g. P2330, P玉晶光, P台股, P外匯
  K{ticker|name}   daily candlestick (6 months)    e.g. K2330, K玉晶光, KTSLA
  KW{ticker|name}  weekly candlestick (2 years)    e.g. KW2330, KW台積電
  KM{ticker|name}  monthly candlestick (5 years)   e.g. KM2330, KMAAPL
  T{ticker|name}   institutional net buy/sell      e.g. T2330, T元太 (OTC), T台股 (market)
  D{ticker|name}   annual dividend & yield chart   e.g. D2330, D0050, DAAPL
  F{ticker|name}   fundamentals, revenue & margin  e.g. F2330, F玉晶光, FAAPL
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from app import config, flex
from app.stock import aliases, charts, data, fundamentals, institutional, tw_directory

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockv")

app = FastAPI(title="StockV")
handler = WebhookHandler(config.LINE_CHANNEL_SECRET or "placeholder")
line_config = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)

os.makedirs(config.CHART_DIR, exist_ok=True)

_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_HASH_CMD = re.compile(r"^#\s*([^\s]{1,20})$")
_MULTI_K_CMD = re.compile(
    r"^(KW|KM)(?:\s*([\u4e00-\u9fff][^\s]{0,19})|\s*(\d[0-9A-Za-z]{2,6})|\s+([0-9A-Za-z][^\s]{0,19})|([0-9A-Za-z][^\s]{1,19}))$",
    re.IGNORECASE,
)
_SINGLE_CMD = re.compile(
    r"^([PKTDF])(?:\s*([\u4e00-\u9fff][^\s]{0,19})|\s*(\d[0-9A-Za-z]{2,6})|([0-9A-Za-z][^\s]{0,19}))$",
    re.IGNORECASE,
)
_VALID_POSTBACK_CMDS = {"#", "P", "K", "KW", "KM", "T", "D", "F"}
_HELP_WORDS = {"help", "幫助", "指令", "說明", "使用說明"}

_base_url_from_request: str = ""


def _parse_command(text: str) -> tuple[str, str] | None:
    """Parse a StockV command into (kind, query), or None if not a command."""
    s = text.strip()
    if not s:
        return None

    m = _HASH_CMD.match(s)
    if m:
        return "#", m.group(1)

    m = _MULTI_K_CMD.match(s)
    if m:
        target = m.group(2) or m.group(3) or m.group(4) or m.group(5)
        return m.group(1).upper(), target

    m = _SINGLE_CMD.match(s)
    if m:
        target = m.group(2) or m.group(3) or m.group(4)
        return m.group(1).upper(), target

    return None


def _parse_postback(raw_data: str) -> tuple[str, str] | None:
    """Parse postback query string like 'cmd=KW&q=7822' into (kind, query)."""
    qs = parse_qs(raw_data or "")
    cmd = (qs.get("cmd") or [""])[0].strip().upper()
    query = (qs.get("q") or [""])[0].strip()
    if cmd in _VALID_POSTBACK_CMDS and query:
        return cmd, query
    return None


def _base_url() -> str:
    return config.BASE_URL or _base_url_from_request


def _new_chart_path() -> tuple[str, str]:
    """(filesystem path, public URL)"""
    name = f"{uuid.uuid4().hex}.png"
    return os.path.join(config.CHART_DIR, name), f"{_base_url()}/charts/{name}"


def _cleanup_charts() -> None:
    cutoff = time.time() - config.CHART_TTL_SECONDS
    try:
        for f in os.listdir(config.CHART_DIR):
            p = os.path.join(config.CHART_DIR, f)
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass


def _start_loading(event) -> None:
    """Show LINE's native typing/loading animation in 1-on-1 chats."""
    source = getattr(event, "source", None)
    if getattr(source, "type", None) != "user":
        return
    user_id = getattr(source, "user_id", None)
    if not user_id:
        return
    try:
        with ApiClient(line_config) as client:
            MessagingApi(client).show_loading_animation(
                ShowLoadingAnimationRequest(chat_id=user_id, loading_seconds=15)
            )
    except Exception:
        log.debug("show_loading_animation failed", exc_info=True)


def _reply(reply_token: str, messages: list) -> None:
    with ApiClient(line_config) as client:
        MessagingApi(client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


def _handle_command(kind: str, query: str) -> list:
    """Build LINE Flex reply messages for one command. Raises on data errors."""
    kind = kind.upper()
    query = query.strip()

    # Special: FX overview table (P外匯 / #外匯)
    if kind in ("#", "P") and aliases.is_fx_overview(query):
        return [flex.fx_overview_card(data.fx_overview())]

    # Quick quote card (#)
    if kind == "#":
        quote = data.resolve(query)
        snap = data.quick_quote(quote)
        val = fundamentals.lookup_valuation(quote.ticker) if (quote.is_taiwan and quote.asset_type == "stock") else None
        return [flex.quote_card(snap, val)]

    # Institutional net buy/sell (T): TWSE listed, TPEx OTC, and TWSE market
    if kind == "T":
        if aliases.is_taiwan_market(query):
            report = institutional.fetch_market()
            path, url = _new_chart_path()
            charts.institutional_chart(report, path)
            return [flex.institutional_card(report, url)]

        tw = tw_directory.lookup(query)
        if tw is not None:
            ticker, market = tw.code, tw.market
        elif query.isdigit():
            ticker, market = query, None
        elif _HAS_CJK.search(query):
            raise data.StockNotFoundError(query)
        else:
            return [TextMessage(text="T 指令支援台股上市/上櫃與大盤，例如 T2330、T元太、T台股")]
        report = institutional.fetch(ticker, market=market)
        path, url = _new_chart_path()
        charts.institutional_chart(report, path)
        return [flex.institutional_card(report, url)]

    # Dividend policy chart (D)
    if kind == "D":
        quote = data.resolve(query)
        report = data.dividends(quote)
        path, url = _new_chart_path()
        charts.dividend_chart(report, path)
        return [flex.dividend_card(report, url)]

    # Fundamentals / valuation / monthly revenue / margin (F)
    if kind == "F":
        quote = data.resolve(query)
        report = fundamentals.fetch(quote)
        return [flex.fundamental_card(report)]

    # Intraday (P) or Candlesticks (K / KW / KM)
    quote = data.resolve(query)
    path, url = _new_chart_path()
    if kind == "P":
        df, prev_close = data.intraday(quote)
        charts.intraday_chart(quote, df, prev_close, path)
        return [flex.intraday_card(quote, df, prev_close, url)]

    tf = "D" if kind == "K" else kind[1]
    _, _, label = data.KLINE_SPECS[tf]
    date_fmt = "%m/%d" if tf == "D" else "%y/%m"
    df = data.kline_history(quote, timeframe=tf)
    charts.candlestick_chart(quote, df, path, period_label=label, date_fmt=date_fmt)
    return [flex.candlestick_card(quote, df, url, timeframe=tf)]


def _execute_and_reply(event, kind: str, query: str, is_group: bool) -> None:
    _start_loading(event)
    try:
        _cleanup_charts()
        messages = _handle_command(kind, query)
    except data.StockNotFoundError:
        if is_group and _HAS_CJK.search(query):
            return  # avoid false positives on casual Chinese chat in groups
        messages = [TextMessage(
            text=f"❓ 找不到「{query}」的報價資料，請確認代號或名稱是否正確。")]
    except institutional.InstitutionalDataUnavailable:
        messages = [TextMessage(
            text=f"❓ 查不到「{query}」的法人資料（支援台股上市/上櫃股票與 T台股）。")]
    except data.DividendDataUnavailable:
        messages = [TextMessage(
            text=f"❓ 查不到「{query}」的歷史配息資料。")]
    except fundamentals.FundamentalDataUnavailable:
        messages = [TextMessage(
            text=f"❓ 查不到「{query}」的基本面資料（僅支援台/美股個股）。")]
    except Exception:
        log.exception("command failed: %s %s", kind, query)
        messages = [TextMessage(text="⚠️ 系統忙碌中，請稍後再試一次。")]

    _reply(event.reply_token, messages)


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event: MessageEvent) -> None:
    text = event.message.text.strip()
    is_group = getattr(getattr(event, "source", None), "type", "user") != "user"

    if text.lower() in _HELP_WORDS:
        _reply(event.reply_token, [flex.help_card()])
        return

    parsed = _parse_command(text)
    if not parsed:
        # In 1-on-1 chats, allow bare stock code/name or alias (e.g. '2330', '玉晶光', '台股', '外匯')
        if not is_group and (
            aliases.is_fx_overview(text)
            or aliases.lookup(text) is not None
            or tw_directory.lookup(text) is not None
        ):
            parsed = ("#", text)
        else:
            return  # not for us — stay quiet in group chats

    kind, query = parsed
    _execute_and_reply(event, kind, query, is_group)


@handler.add(PostbackEvent)
def on_postback(event: PostbackEvent) -> None:
    parsed = _parse_postback(getattr(getattr(event, "postback", None), "data", ""))
    if not parsed:
        return
    is_group = getattr(getattr(event, "source", None), "type", "user") != "user"
    kind, query = parsed
    _execute_and_reply(event, kind, query, is_group)


@app.post("/webhook")
async def webhook(request: Request):
    global _base_url_from_request
    if not _base_url_from_request:
        host = request.headers.get("host", "")
        if host:
            _base_url_from_request = f"https://{host}"

    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(handler.handle, body, signature)
    except InvalidSignatureError:
        raise HTTPException(400, "invalid signature")
    return PlainTextResponse("OK")


@app.get("/charts/{name}")
def get_chart(name: str):
    if not re.fullmatch(r"[0-9a-f]{32}\.png", name):
        raise HTTPException(404)
    path = os.path.join(config.CHART_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


@app.get("/")
def health():
    return PlainTextResponse("StockV OK")

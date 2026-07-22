"""StockV — LINE stock analytics bot.

Commands (case-insensitive, no spaces):
  P{ticker}  intraday price chart          e.g. P2330, PAAPL
  K{ticker}  daily candlestick, 6 months   e.g. K2330, KTSLA
  T{ticker}  institutional net buy/sell    e.g. T2330 (Taiwan listed only)
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    ImageMessage,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app import config
from app.stock import charts, data, institutional

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockv")

app = FastAPI(title="StockV")
handler = WebhookHandler(config.LINE_CHANNEL_SECRET or "placeholder")
line_config = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)

os.makedirs(config.CHART_DIR, exist_ok=True)

_COMMAND = re.compile(r"^([PKT])([0-9A-Z.\-]{1,10})$", re.IGNORECASE)
_HELP_WORDS = {"help", "幫助", "指令", "說明", "使用說明"}

HELP_TEXT = (
    "📈 StockV 指令說明\n"
    "──────────────\n"
    "P+代號｜當日走勢圖\n"
    "　例：P2330、PAAPL\n"
    "K+代號｜日K線圖（近6個月）\n"
    "　例：K2330、KTSLA\n"
    "T+代號｜三大法人買賣超（台股上市）\n"
    "　例：T2330\n"
    "──────────────\n"
    "台股輸入數字代號，美股輸入英文代號"
)

_base_url_from_request: str = ""


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


def _fmt_lots(shares: float) -> str:
    lots = shares / 1000
    return f"{lots:+,.0f}"


def _handle_command(kind: str, ticker: str) -> list:
    """Build LINE reply messages for one command. Raises on data errors."""
    kind = kind.upper()
    ticker = ticker.upper()

    if kind == "T":
        if not ticker.isdigit():
            return [TextMessage(text="T 指令僅支援台股（數字代號），例如 T2330")]
        report = institutional.fetch(ticker)
        path, url = _new_chart_path()
        charts.institutional_chart(report, path)
        latest = report.days[-1]
        lines = [
            f"🏦 {report.name} ({ticker}) 三大法人",
            f"📅 {latest.day:%Y-%m-%d} 買賣超（張）",
            f"　外資：{_fmt_lots(latest.foreign)}",
            f"　投信：{_fmt_lots(latest.trust)}",
            f"　自營商：{_fmt_lots(latest.dealer)}",
            f"　合計：{_fmt_lots(latest.total)}",
        ]
        if report.foreign_holding_pct is not None:
            lines.append(f"🌍 外資持股比率：{report.foreign_holding_pct:.2f}%")
        return [
            ImageMessage(original_content_url=url, preview_image_url=url),
            TextMessage(text="\n".join(lines)),
        ]

    quote = data.resolve(ticker)
    path, url = _new_chart_path()
    if kind == "P":
        df, prev_close = data.intraday(quote)
        charts.intraday_chart(quote, df, prev_close, path)
    else:  # K
        df = data.daily_history(quote, months=6)
        charts.candlestick_chart(quote, df, path)
    return [ImageMessage(original_content_url=url, preview_image_url=url)]


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event: MessageEvent) -> None:
    text = event.message.text.strip()
    messages = None

    if text.lower() in _HELP_WORDS:
        messages = [TextMessage(text=HELP_TEXT)]
    else:
        m = _COMMAND.match(text)
        if not m:
            return  # not for us — stay quiet in group chats
        kind, ticker = m.group(1), m.group(2)
        try:
            _cleanup_charts()
            messages = _handle_command(kind, ticker)
        except data.StockNotFoundError:
            messages = [TextMessage(text=f"❓ 找不到「{ticker}」的股價資料，請確認代號是否正確。")]
        except institutional.InstitutionalDataUnavailable:
            messages = [TextMessage(
                text=f"❓ 查不到「{ticker}」的法人資料（目前僅支援台股上市股票）。")]
        except Exception:
            log.exception("command failed: %s", text)
            messages = [TextMessage(text="⚠️ 系統忙碌中，請稍後再試一次。")]

    with ApiClient(line_config) as client:
        MessagingApi(client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=messages))


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

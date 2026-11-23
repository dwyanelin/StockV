"""Stock data fetching: Yahoo Finance for prices (TW + US), TWSE for institutional data."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from app.stock import tw_directory

log = logging.getLogger(__name__)
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


class StockNotFoundError(Exception):
    """Raised when a ticker cannot be resolved to any market."""


@dataclass
class StockQuote:
    ticker: str          # user-facing ticker, e.g. "2330" or "AAPL"
    yahoo_symbol: str    # e.g. "2330.TW" or "AAPL"
    name: str
    is_taiwan: bool
    currency: str


def _has_data(symbol: str) -> bool:
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="1d", raise_errors=False)
        return not df.empty
    except Exception:
        return False


def resolve(ticker: str) -> StockQuote:
    """Map a user ticker or Taiwan stock name to a Yahoo Finance symbol."""
    raw = ticker.strip()
    if not raw:
        raise StockNotFoundError(ticker)

    tw = tw_directory.lookup(raw)
    if tw is not None:
        alt_suffix = ".TWO" if tw.yahoo_suffix == ".TW" else ".TW"
        for symbol in (tw.yahoo_symbol, f"{tw.code}{alt_suffix}"):
            if _has_data(symbol):
                return StockQuote(
                    ticker=tw.code,
                    yahoo_symbol=symbol,
                    name=tw.name,
                    is_taiwan=True,
                    currency="TWD",
                )
        raise StockNotFoundError(raw)

    if _HAS_CJK.search(raw):
        raise StockNotFoundError(raw)

    code = raw.upper()
    is_taiwan = code.isdigit()
    candidates = [f"{code}.TW", f"{code}.TWO"] if is_taiwan else [code]

    for symbol in candidates:
        if not _has_data(symbol):
            continue
        name, currency = code, "TWD" if is_taiwan else "USD"
        try:
            info = yf.Ticker(symbol).info
            name = info.get("longName") or info.get("shortName") or code
            currency = info.get("currency") or currency
        except Exception:
            log.warning("info lookup failed for %s", symbol, exc_info=True)
        return StockQuote(ticker=code, yahoo_symbol=symbol, name=name,
                          is_taiwan=is_taiwan, currency=currency)

    raise StockNotFoundError(raw)


def intraday(quote: StockQuote) -> tuple[pd.DataFrame, float | None]:
    """1-minute bars for the most recent trading day, plus previous close."""
    tk = yf.Ticker(quote.yahoo_symbol)
    df = tk.history(period="1d", interval="1m")
    if df.empty:
        df = tk.history(period="5d", interval="5m")
        if df.empty:
            raise StockNotFoundError(quote.ticker)
        last_day = df.index[-1].date()
        df = df[df.index.date == last_day]

    prev_close = None
    try:
        daily = tk.history(period="10d", interval="1d")
        if len(daily) >= 2 and daily.index[-1].date() == df.index[-1].date():
            prev_close = float(daily["Close"].iloc[-2])
        elif not daily.empty:
            prev_close = float(daily["Close"].iloc[-1])
    except Exception:
        log.warning("prev close lookup failed for %s", quote.yahoo_symbol, exc_info=True)
    return df, prev_close


def daily_history(quote: StockQuote, months: int = 6) -> pd.DataFrame:
    """Daily OHLCV for candlestick charts."""
    df = yf.Ticker(quote.yahoo_symbol).history(period=f"{months}mo", interval="1d")
    if df.empty:
        raise StockNotFoundError(quote.ticker)
    return df

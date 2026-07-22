"""Stock data fetching: Yahoo Finance for prices (TW + US), TWSE for institutional data."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


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
    """Map a user ticker to a Yahoo symbol. Digits = Taiwan (.TW then .TWO), letters = US."""
    ticker = ticker.strip().upper()
    is_taiwan = ticker.isdigit()
    candidates = [f"{ticker}.TW", f"{ticker}.TWO"] if is_taiwan else [ticker]

    for symbol in candidates:
        if not _has_data(symbol):
            continue
        name, currency = ticker, "TWD" if is_taiwan else "USD"
        try:
            info = yf.Ticker(symbol).info
            name = info.get("longName") or info.get("shortName") or ticker
            currency = info.get("currency") or currency
        except Exception:
            log.warning("info lookup failed for %s", symbol, exc_info=True)
        return StockQuote(ticker=ticker, yahoo_symbol=symbol, name=name,
                          is_taiwan=is_taiwan, currency=currency)

    raise StockNotFoundError(ticker)


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

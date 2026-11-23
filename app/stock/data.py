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


_OHLC = ("Open", "High", "Low", "Close")


def _is_missing(val) -> bool:
    return bool(pd.isna(val))


def _backfill_latest_bar(tk: yf.Ticker, df: pd.DataFrame) -> pd.DataFrame:
    """Repair Yahoo Finance's occasional NaN Close on the latest daily bar."""
    if df.empty or not any(_is_missing(df[col].iloc[-1]) for col in _OHLC if col in df.columns):
        return df

    df = df.copy()
    last_idx = df.index[-1]
    last_date = last_idx.date()

    # 1. Try Yahoo's 1d bar (often has complete OHLC when 6mo has Close=NaN)
    try:
        one_day = tk.history(period="1d", interval="1d")
        if not one_day.empty and one_day.index[-1].date() == last_date:
            for col in ("Open", "High", "Low", "Close", "Volume"):
                if col in df.columns and col in one_day.columns and _is_missing(df.loc[last_idx, col]):
                    val = one_day[col].iloc[-1]
                    if not _is_missing(val):
                        df.loc[last_idx, col] = val
    except Exception:
        log.warning("1d backfill failed for %s", getattr(tk, "ticker", ""), exc_info=True)

    # 2. Fallback to fast_info last_price if Close is still missing
    if "Close" in df.columns and _is_missing(df.loc[last_idx, "Close"]):
        try:
            last_price = getattr(tk.fast_info, "last_price", None)
            if last_price is None and hasattr(tk.fast_info, "get"):
                last_price = tk.fast_info.get("lastPrice")
            if last_price is not None and not _is_missing(last_price):
                price = float(last_price)
                df.loc[last_idx, "Close"] = price
                if "Open" in df.columns and _is_missing(df.loc[last_idx, "Open"]):
                    df.loc[last_idx, "Open"] = price
                if "High" in df.columns and (_is_missing(df.loc[last_idx, "High"]) or price > df.loc[last_idx, "High"]):
                    df.loc[last_idx, "High"] = price
                if "Low" in df.columns and (_is_missing(df.loc[last_idx, "Low"]) or price < df.loc[last_idx, "Low"]):
                    df.loc[last_idx, "Low"] = price
        except Exception:
            log.warning("fast_info backfill failed for %s", getattr(tk, "ticker", ""), exc_info=True)

    return df


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
        daily = tk.history(period="10d", interval="1d").dropna(subset=["Close"])
        if len(daily) >= 2 and daily.index[-1].date() == df.index[-1].date():
            prev_close = float(daily["Close"].iloc[-2])
        elif not daily.empty:
            prev_close = float(daily["Close"].iloc[-1])
    except Exception:
        log.warning("prev close lookup failed for %s", quote.yahoo_symbol, exc_info=True)
    return df, prev_close


def daily_history(quote: StockQuote, months: int = 6) -> pd.DataFrame:
    """Daily OHLCV for candlestick charts."""
    tk = yf.Ticker(quote.yahoo_symbol)
    df = tk.history(period=f"{months}mo", interval="1d")
    if df.empty:
        raise StockNotFoundError(quote.ticker)
    df = _backfill_latest_bar(tk, df)
    ohlc_cols = [c for c in _OHLC if c in df.columns]
    if ohlc_cols:
        df = df.dropna(subset=ohlc_cols)
    if df.empty:
        raise StockNotFoundError(quote.ticker)
    return df

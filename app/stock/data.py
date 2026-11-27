"""Stock & cross-asset data fetching via Yahoo Finance + Taiwan stock directory."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import yfinance as yf

from app.stock import aliases, futures, tw_directory

log = logging.getLogger(__name__)
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
_OHLC = ("Open", "High", "Low", "Close")

KLINE_SPECS: dict[str, tuple[str, str, str]] = {
    # key -> (yf_period, yf_interval, label)
    "D": ("6mo", "1d", "日K 近6個月"),
    "W": ("2y", "1wk", "週K 近2年"),
    "M": ("5y", "1mo", "月K 近5年"),
}


class StockNotFoundError(Exception):
    """Raised when a ticker cannot be resolved to any market."""


class DividendDataUnavailable(Exception):
    """Raised when a symbol has no dividend history."""


@dataclass
class StockQuote:
    ticker: str          # user-facing ticker, e.g. "2330", "AAPL", "BTC", "台股"
    yahoo_symbol: str    # e.g. "2330.TW", "AAPL", "BTC-USD", "^TWII"
    name: str
    is_taiwan: bool
    currency: str
    asset_type: str = "stock"  # "stock", "index", "crypto", "fx", "commodity"


@dataclass
class QuickSnapshot:
    quote: StockQuote
    price: float
    prev_close: float | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    volume: float
    as_of: str


@dataclass
class AnnualDividend:
    year: int
    amount: float


@dataclass
class DividendReport:
    quote: StockQuote
    current_price: float | None
    years: list[AnnualDividend] = field(default_factory=list)
    latest_ex_date: date | None = None
    latest_amount: float = 0.0
    avg_5y: float = 0.0
    yield_pct: float | None = None


def _has_data(symbol: str) -> bool:
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="1d", raise_errors=False)
        return not df.empty
    except Exception:
        return False


def _is_missing(val) -> bool:
    return bool(pd.isna(val))


def resolve(ticker: str) -> StockQuote:
    """Map a user ticker, Taiwan stock name, or cross-asset alias to a Yahoo Finance symbol."""
    raw = ticker.strip()
    if not raw:
        raise StockNotFoundError(ticker)

    # 1. Cross-asset aliases (台股, BTC, USD/TWD, SOX, GOLD, ...)
    alias = aliases.lookup(raw)
    if alias is not None:
        return StockQuote(
            ticker=alias.ticker,
            yahoo_symbol=alias.yahoo_symbol,
            name=alias.name,
            is_taiwan=alias.is_taiwan,
            currency=alias.currency,
            asset_type=alias.asset_type,
        )

    # 2. Taiwan stock directory (TWSE / TPEx / ESB by code or Chinese name)
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
                    asset_type="stock",
                )
        raise StockNotFoundError(raw)

    if _HAS_CJK.search(raw):
        raise StockNotFoundError(raw)

    # 3. Direct Yahoo symbol (US stocks or fallback Taiwan numeric code)
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
        return StockQuote(
            ticker=code,
            yahoo_symbol=symbol,
            name=name,
            is_taiwan=is_taiwan,
            currency=currency,
            asset_type="stock",
        )

    raise StockNotFoundError(raw)


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


def intraday_total_volume(df: pd.DataFrame) -> float:
    """Return authoritative daily volume when attached to df.attrs, else sum of bar volumes."""
    attr_vol = df.attrs.get("total_volume")
    if attr_vol is not None and not _is_missing(attr_vol) and float(attr_vol) > 0:
        return float(attr_vol)
    return float(df["Volume"].dropna().sum()) if "Volume" in df.columns else 0.0


def intraday(quote: StockQuote) -> tuple[pd.DataFrame, float | None]:
    """1-minute (or 5-minute fallback) bars for the most recent trading day, plus previous close."""
    if quote.yahoo_symbol.startswith("TAIFEX:"):
        cid = quote.yahoo_symbol.split(":", 1)[1]
        try:
            qinfo, df, prev_close = futures.fetch_intraday(cid)
        except futures.FuturesDataUnavailable:
            raise StockNotFoundError(quote.ticker) from None
        quote.name = qinfo["name"]
        return df, prev_close

    # Taiwan equities & cash indices have no extended-hours session on Yahoo Finance,
    # yet their official closing-auction / close bar sits at the exact market-close
    # timestamp (e.g. 13:30:00 CST for TWSE/TPEx/^TWII). yfinance's default prepost=False
    # filters out `index >= tradingPeriods.end`, which drops the 13:30 closing bar and
    # freezes Taiwan intraday prices at 13:24 (before the 5-min closing call auction).
    include_close = quote.is_taiwan or quote.asset_type == "index"

    tk = yf.Ticker(quote.yahoo_symbol)
    df = tk.history(period="1d", interval="1m", prepost=include_close)
    if df.empty:
        df = tk.history(period="5d", interval="5m", prepost=include_close)
        if df.empty:
            df = tk.history(period="5d", interval="15m", prepost=include_close)
        if df.empty:
            raise StockNotFoundError(quote.ticker)
    last_day = df.index[-1].date()
    df = df[df.index.date == last_day].copy()

    prev_close = None
    try:
        daily = tk.history(period="10d", interval="1d").dropna(subset=["Close"])
        if not daily.empty and daily.index[-1].date() == last_day:
            if len(daily) >= 2:
                prev_close = float(daily["Close"].iloc[-2])
            if "Volume" in daily.columns and not _is_missing(daily["Volume"].iloc[-1]):
                day_vol = float(daily["Volume"].iloc[-1])
                if day_vol > 0:
                    df.attrs["total_volume"] = day_vol
        elif not daily.empty:
            prev_close = float(daily["Close"].iloc[-1])
    except Exception:
        log.warning("prev close lookup failed for %s", quote.yahoo_symbol, exc_info=True)
    return df, prev_close


def kline_history(quote: StockQuote, timeframe: str = "D") -> pd.DataFrame:
    """OHLCV history for daily ('D'), weekly ('W'), or monthly ('M') candlestick charts."""
    if quote.yahoo_symbol.startswith("TAIFEX:"):
        # Fallback to Taiwan spot index (^TWII) for multi-month K-line charts
        quote = StockQuote(quote.ticker, "^TWII", f"{quote.name} (參考加權)", True, "TWD", "index")
    tf = timeframe.upper()
    period, interval, _ = KLINE_SPECS.get(tf, KLINE_SPECS["D"])
    tk = yf.Ticker(quote.yahoo_symbol)
    df = tk.history(period=period, interval=interval)
    if df.empty:
        raise StockNotFoundError(quote.ticker)
    df = _backfill_latest_bar(tk, df)
    ohlc_cols = [c for c in _OHLC if c in df.columns]
    if ohlc_cols:
        df = df.dropna(subset=ohlc_cols)
    if df.empty:
        raise StockNotFoundError(quote.ticker)
    return df


def daily_history(quote: StockQuote, months: int = 6) -> pd.DataFrame:
    """Daily OHLCV for candlestick charts (kept for backward compatibility)."""
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


def quick_quote(quote: StockQuote) -> QuickSnapshot:
    """Fast quote snapshot without rendering any chart."""
    if quote.yahoo_symbol.startswith("TAIFEX:"):
        cid = quote.yahoo_symbol.split(":", 1)[1]
        try:
            qinfo = futures.fetch_quote(cid)
        except futures.FuturesDataUnavailable:
            raise StockNotFoundError(quote.ticker) from None
        quote.name = qinfo["name"]
        return QuickSnapshot(
            quote=quote,
            price=qinfo["price"],
            prev_close=qinfo["prev_close"],
            open_price=qinfo["open"],
            high_price=qinfo["high"],
            low_price=qinfo["low"],
            volume=qinfo["volume"],
            as_of=qinfo["as_of"],
        )

    tk = yf.Ticker(quote.yahoo_symbol)
    daily = tk.history(period="5d", interval="1d")
    if daily.empty:
        raise StockNotFoundError(quote.ticker)
    daily = _backfill_latest_bar(tk, daily).dropna(subset=["Close"])
    if daily.empty:
        raise StockNotFoundError(quote.ticker)

    last_row = daily.iloc[-1]
    price = float(last_row["Close"])
    prev_close = float(daily["Close"].iloc[-2]) if len(daily) >= 2 else None
    open_px = float(last_row["Open"]) if "Open" in daily.columns and not _is_missing(last_row["Open"]) else None
    high_px = float(last_row["High"]) if "High" in daily.columns and not _is_missing(last_row["High"]) else None
    low_px = float(last_row["Low"]) if "Low" in daily.columns and not _is_missing(last_row["Low"]) else None
    vol = float(last_row["Volume"]) if "Volume" in daily.columns and not _is_missing(last_row["Volume"]) else 0.0
    as_of = daily.index[-1].strftime("%Y/%m/%d")

    return QuickSnapshot(
        quote=quote,
        price=price,
        prev_close=prev_close,
        open_price=open_px,
        high_price=high_px,
        low_price=low_px,
        volume=vol,
        as_of=as_of,
    )


def dividends(quote: StockQuote, max_years: int = 10) -> DividendReport:
    """Annual dividend history and estimated yield for a stock or ETF."""
    tk = yf.Ticker(quote.yahoo_symbol)
    divs = tk.dividends
    if divs is None or divs.empty:
        raise DividendDataUnavailable(quote.ticker)

    divs = divs[divs > 0]
    if divs.empty:
        raise DividendDataUnavailable(quote.ticker)

    by_year = divs.groupby(divs.index.year).sum()
    years = [AnnualDividend(year=int(y), amount=float(v)) for y, v in by_year.items()][-max_years:]
    latest_ex_date = divs.index[-1].date()

    # Latest full/trailing 12-month dividend sum for realistic yield estimate
    cutoff = divs.index[-1] - pd.Timedelta(days=365)
    ttm_div = float(divs[divs.index > cutoff].sum())
    latest_amount = years[-1].amount

    recent5 = years[-5:]
    avg_5y = sum(x.amount for x in recent5) / len(recent5) if recent5 else 0.0

    current_price = None
    try:
        hist = tk.history(period="5d", interval="1d")
        hist = _backfill_latest_bar(tk, hist).dropna(subset=["Close"])
        if not hist.empty:
            current_price = float(hist["Close"].iloc[-1])
    except Exception:
        log.warning("dividend price lookup failed for %s", quote.yahoo_symbol, exc_info=True)

    yield_pct = (ttm_div / current_price * 100.0) if (current_price and current_price > 0 and ttm_div > 0) else None

    return DividendReport(
        quote=quote,
        current_price=current_price,
        years=years,
        latest_ex_date=latest_ex_date,
        latest_amount=latest_amount,
        avg_5y=avg_5y,
        yield_pct=yield_pct,
    )


def fx_overview() -> list[QuickSnapshot]:
    """Fetch key international FX pairs in parallel."""
    quotes = [
        StockQuote(a.ticker, a.yahoo_symbol, a.name, a.is_taiwan, a.currency, a.asset_type)
        for a in aliases.FX_OVERVIEW_PAIRS
    ]

    def _safe_quote(q: StockQuote) -> QuickSnapshot | None:
        try:
            return quick_quote(q)
        except Exception:
            log.warning("FX quote failed for %s", q.yahoo_symbol, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=len(quotes)) as pool:
        results = [r for r in pool.map(_safe_quote, quotes) if r is not None]
    if not results:
        raise StockNotFoundError("外匯")
    return results

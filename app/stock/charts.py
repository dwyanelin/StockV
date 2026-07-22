"""Chart rendering with matplotlib / mplfinance.

Conventions: Taiwan stocks use red=up / green=down; US stocks the opposite.
All charts share a dark, clean look designed to read well inside LINE.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from app.stock.data import StockQuote
from app.stock.institutional import InstReport

BG = "#131722"
PANEL = "#131722"
GRID = "#2a2e39"
TEXT = "#d1d4dc"
BLUE = "#2962ff"

_CJK_CANDIDATES = ["Noto Sans CJK TC", "Noto Sans CJK JP", "Noto Sans TC",
                   "PingFang TC", "Heiti TC", "Microsoft JhengHei",
                   "Arial Unicode MS"]


def _setup_fonts() -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    chosen = [f for f in _CJK_CANDIDATES if f in available]
    plt.rcParams["font.sans-serif"] = chosen + plt.rcParams["font.sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False


_setup_fonts()


def _short(name: str, limit: int = 26) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _colors(is_taiwan: bool) -> tuple[str, str]:
    """(up_color, down_color)"""
    return ("#f6465d", "#2ebd85") if is_taiwan else ("#2ebd85", "#f6465d")


def _base_ax(figsize=(10, 6)):
    fig, ax = plt.subplots(figsize=figsize, dpi=120)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.7)
    ax.tick_params(colors=TEXT, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    return fig, ax


def _save(fig, path: str) -> str:
    fig.savefig(path, facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return path


def intraday_chart(quote: StockQuote, df: pd.DataFrame, prev_close: float | None,
                   path: str) -> str:
    """Intraday line chart with change coloring and previous-close reference."""
    close = df["Close"].dropna()
    last = float(close.iloc[-1])
    ref = prev_close if prev_close else float(close.iloc[0])
    change, pct = last - ref, (last - ref) / ref * 100
    up, down = _colors(quote.is_taiwan)
    color = up if change >= 0 else down

    fig, ax = _base_ax()
    ax.plot(close.index, close.values, color=color, linewidth=1.6)
    ax.fill_between(close.index, close.values, ref, color=color, alpha=0.12)
    if prev_close:
        ax.axhline(ref, color=TEXT, linewidth=0.8, linestyle="--", alpha=0.5)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=close.index.tz))
    sign = "+" if change >= 0 else ""
    ax.set_title(
        f"{_short(quote.name)} ({quote.ticker})  {last:,.2f}  "
        f"{sign}{change:,.2f} ({sign}{pct:.2f}%)",
        color=color, fontsize=13, fontweight="bold", loc="left", pad=12,
    )
    day = close.index[-1].strftime("%Y-%m-%d")
    ax.text(0.99, 0.03, f"{day} 走勢圖", transform=ax.transAxes,
            color=TEXT, fontsize=9, ha="right", va="bottom", alpha=0.8)
    return _save(fig, path)


def candlestick_chart(quote: StockQuote, df: pd.DataFrame, path: str) -> str:
    """Daily candlestick chart with volume and 5/20/60-day moving averages."""
    up, down = _colors(quote.is_taiwan)
    mc = mpf.make_marketcolors(up=up, down=down, edge="inherit",
                               wick="inherit", volume={"up": up, "down": down})
    style = mpf.make_mpf_style(
        marketcolors=mc, facecolor=PANEL, figcolor=BG, edgecolor=GRID,
        gridcolor=GRID, gridstyle="-",
        rc={"axes.labelcolor": TEXT, "xtick.color": TEXT, "ytick.color": TEXT,
            "font.sans-serif": plt.rcParams["font.sans-serif"],
            "axes.unicode_minus": False},
    )
    last, first = float(df["Close"].iloc[-1]), float(df["Close"].iloc[0])
    pct = (last - first) / first * 100
    sign = "+" if pct >= 0 else ""

    fig, _ = mpf.plot(
        df, type="candle", volume=True, style=style,
        mav=(5, 20, 60), mavcolors=["#f0b90b", "#2962ff", "#b39ddb"],
        figsize=(10, 7), returnfig=True, tight_layout=True,
        datetime_format="%m/%d", xrotation=0,
    )
    fig.suptitle(
        f"{_short(quote.name)} ({quote.ticker})  日K 近6個月   收 {last:,.2f}  ({sign}{pct:.1f}%)",
        color=TEXT, fontsize=13, fontweight="bold", x=0.05, ha="left",
    )
    fig.text(0.05, 0.945, "MA5", color="#f0b90b", fontsize=9)
    fig.text(0.10, 0.945, "MA20", color="#2962ff", fontsize=9)
    fig.text(0.16, 0.945, "MA60", color="#b39ddb", fontsize=9)
    return _save(fig, path)


def institutional_chart(report: InstReport, path: str) -> str:
    """Grouped bars: daily net buy/sell (in 張, lots of 1000 shares) per group."""
    days = report.days
    x = range(len(days))
    labels = [d.day.strftime("%m/%d") for d in days]
    groups = [
        ("外資", [d.foreign / 1000 for d in days], "#2962ff"),
        ("投信", [d.trust / 1000 for d in days], "#f0b90b"),
        ("自營商", [d.dealer / 1000 for d in days], "#b39ddb"),
    ]

    fig, ax = _base_ax(figsize=(10, 6))
    width = 0.27
    for i, (label, values, color) in enumerate(groups):
        ax.bar([xi + (i - 1) * width for xi in x], values, width=width,
               label=label, color=color)
    ax.axhline(0, color=TEXT, linewidth=0.8, alpha=0.6)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("買賣超（張）", color=TEXT)
    legend = ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    title = f"{report.name} ({report.ticker})  三大法人買賣超"
    if report.foreign_holding_pct is not None:
        title += f"   外資持股 {report.foreign_holding_pct:.2f}%"
    ax.set_title(title, color=TEXT, fontsize=13, fontweight="bold", loc="left", pad=12)
    return _save(fig, path)

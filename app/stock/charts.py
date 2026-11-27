"""Chart rendering with matplotlib / mplfinance.

Conventions: Taiwan stocks use red=up / green=down; US stocks the opposite.
All charts share a crisp TradingView-inspired dark theme tailored for LINE.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from matplotlib.ticker import FuncFormatter

from app.stock.data import DividendReport, StockQuote, intraday_total_volume
from app.stock.institutional import InstReport

BG = "#0f141e"
PANEL = "#131926"
GRID = "#232b3b"
TEXT = "#e2e8f0"
MUTED = "#8b98b1"
BLUE = "#3b82f6"
YELLOW = "#f59e0b"
PURPLE = "#a78bfa"

_CJK_CANDIDATES = [
    "Noto Sans CJK TC",
    "Noto Sans CJK JP",
    "Noto Sans TC",
    "PingFang TC",
    "Heiti TC",
    "Microsoft JhengHei",
    "Arial Unicode MS",
]


def _setup_fonts() -> None:
    available = {f.name for f in fm.fontManager.ttflist}
    chosen = [f for f in _CJK_CANDIDATES if f in available]
    plt.rcParams["font.sans-serif"] = chosen + plt.rcParams["font.sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False


_setup_fonts()


def _short(name: str, limit: int = 24) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _colors(is_taiwan: bool) -> tuple[str, str]:
    """(up_color, down_color)"""
    return ("#f6465d", "#2ebd85") if is_taiwan else ("#2ebd85", "#f6465d")


def _fmt_price(val: float, _pos=None) -> str:
    if abs(val) >= 1000:
        return f"{val:,.0f}"
    if abs(val) >= 100:
        return f"{val:,.1f}"
    if abs(val) < 1 and val != 0:
        return f"{val:.4f}"
    return f"{val:,.2f}"


def _price_str(val: float) -> str:
    if abs(val) < 1 and val != 0:
        return f"{val:.4f}"
    return f"{val:,.2f}"


def _fmt_vol_axis(is_taiwan: bool):
    def _fmt(val: float, _pos=None) -> str:
        if val <= 0:
            return "0"
        if is_taiwan:
            lots = val / 1000.0
            if lots >= 10000:
                return f"{lots / 10000:.1f}萬張"
            if lots >= 1:
                return f"{lots:,.0f}張"
            return f"{lots:.1f}張"
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M"
        if val >= 1_000:
            return f"{val / 1_000:.0f}K"
        return f"{val:.0f}"

    return FuncFormatter(_fmt)


def _fmt_vol_summary(shares: float, is_taiwan: bool) -> str:
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


def _style_ax(ax, right_axis: bool = True) -> None:
    ax.set_facecolor(PANEL)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.75)
    ax.tick_params(colors=MUTED, labelsize=8.5, length=3)
    for spine in ax.spines.values():
        spine.set_color(GRID)
        spine.set_linewidth(0.8)
    if right_axis:
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")


def _add_price_pill(ax, price: float, color: str) -> None:
    """TradingView-style current price pill tag on the right Y-axis."""
    ax.axhline(price, color=color, linewidth=0.7, linestyle=":", alpha=0.65)
    ax.annotate(
        f" {_price_str(price)} ",
        xy=(1.0, price),
        xycoords=("axes fraction", "data"),
        xytext=(4, 0),
        textcoords="offset points",
        va="center",
        ha="left",
        fontsize=8.5,
        fontweight="bold",
        color="#ffffff",
        bbox=dict(boxstyle="round,pad=0.22", facecolor=color, edgecolor="none"),
        annotation_clip=False,
    )


def _save(fig, path: str) -> str:
    fig.savefig(path, facecolor=BG, dpi=150)
    plt.close(fig)
    return path


def intraday_chart(
    quote: StockQuote, df: pd.DataFrame, prev_close: float | None, path: str
) -> str:
    """Intraday split-color line chart + volume bars + right-axis price pill."""
    close = df["Close"].dropna()
    last = float(close.iloc[-1])
    open_px = float(df["Open"].dropna().iloc[0]) if "Open" in df.columns and not df["Open"].dropna().empty else float(close.iloc[0])
    high_px = float(df["High"].dropna().max()) if "High" in df.columns and not df["High"].dropna().empty else float(close.max())
    low_px = float(df["Low"].dropna().min()) if "Low" in df.columns and not df["Low"].dropna().empty else float(close.min())
    bar_vol_sum = float(df["Volume"].dropna().sum()) if "Volume" in df.columns else 0.0
    total_vol = intraday_total_volume(df)

    ref = prev_close if prev_close else open_px
    change = last - ref
    pct = (change / ref * 100) if ref else 0.0
    up, down = _colors(quote.is_taiwan)
    color = up if change >= 0 else down
    arrow = "▲" if change > 0 else ("▼" if change < 0 else "─")
    sign = "+" if change > 0 else ""

    has_vol = "Volume" in df.columns and bar_vol_sum > 0
    if has_vol:
        fig, (ax, ax_vol) = plt.subplots(
            2, 1, figsize=(10, 6.25), dpi=150, sharex=True,
            gridspec_kw={"height_ratios": [4.2, 1.0], "hspace": 0.06},
        )
    else:
        fig, ax = plt.subplots(figsize=(10, 6.25), dpi=150)
        ax_vol = None

    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(top=0.84, bottom=0.09, left=0.04, right=0.89)
    _style_ax(ax, right_axis=True)

    # Split-color area fill relative to previous close
    ax.plot(close.index, close.values, color=color, linewidth=1.8, zorder=4)
    ax.fill_between(
        close.index, close.values, ref,
        where=(close.values >= ref), interpolate=True, color=up, alpha=0.16, zorder=2,
    )
    ax.fill_between(
        close.index, close.values, ref,
        where=(close.values <= ref), interpolate=True, color=down, alpha=0.16, zorder=2,
    )

    if prev_close:
        ax.axhline(ref, color=MUTED, linewidth=0.9, linestyle="--", alpha=0.6, zorder=3)
        ax.text(
            0.01, ref, f" 昨收 {ref:,.2f}",
            transform=ax.get_yaxis_transform(),
            color=MUTED, fontsize=8, va="bottom", ha="left",
            bbox=dict(boxstyle="round,pad=0.15", facecolor=PANEL, edgecolor="none", alpha=0.85),
        )

    # High / Low callout markers
    hi_idx = close.idxmax()
    lo_idx = close.idxmin()
    y_min, y_max = float(close.min()), float(close.max())
    if prev_close:
        y_min, y_max = min(y_min, ref), max(y_max, ref)
    pad = max((y_max - y_min) * 0.14, y_max * 0.003, 0.01)
    ax.set_ylim(y_min - pad, y_max + pad)

    ax.scatter([hi_idx], [close.loc[hi_idx]], color=up, s=22, zorder=5)
    ax.annotate(
        f"高 {high_px:,.2f}",
        xy=(hi_idx, close.loc[hi_idx]), xytext=(0, 7), textcoords="offset points",
        color=TEXT, fontsize=8, ha="center", va="bottom",
    )
    ax.scatter([lo_idx], [close.loc[lo_idx]], color=down, s=22, zorder=5)
    ax.annotate(
        f"低 {low_px:,.2f}",
        xy=(lo_idx, close.loc[lo_idx]), xytext=(0, -11), textcoords="offset points",
        color=TEXT, fontsize=8, ha="center", va="top",
    )

    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_price))
    _add_price_pill(ax, last, color)

    target_ax = ax_vol if ax_vol is not None else ax
    if ax_vol is not None:
        _style_ax(ax_vol, right_axis=True)
        vol = df["Volume"].reindex(close.index).fillna(0)
        bar_colors = [
            up if (i == 0 and close.iloc[i] >= ref) or (i > 0 and close.iloc[i] >= close.iloc[i - 1]) else down
            for i in range(len(close))
        ]
        # Bar width in matplotlib date units (days)
        if len(close) >= 2:
            diffs = close.index.to_series().diff().dropna().dt.total_seconds()
            step_days = max(float(diffs.median()) / 86400.0, 1 / 1440.0)
        else:
            step_days = 1 / 1440.0
        ax_vol.bar(close.index, vol.values, width=step_days * 0.72, color=bar_colors, alpha=0.65)
        ax_vol.yaxis.set_major_formatter(_fmt_vol_axis(quote.is_taiwan))
        ax_vol.yaxis.set_major_locator(plt.MaxNLocator(2))
        ax.tick_params(labelbottom=False)

    target_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=close.index.tz))

    # Clean two-line header above chart (no overlap with plot)
    fig.text(
        0.04, 0.935, f"{_short(quote.name)} ({quote.ticker})",
        color=TEXT, fontsize=14, fontweight="bold", ha="left", va="center",
    )
    fig.text(
        0.89, 0.935, f"{last:,.2f}  {arrow} {sign}{change:,.2f} ({sign}{pct:.2f}%)",
        color=color, fontsize=13.5, fontweight="bold", ha="right", va="center",
    )
    day_str = close.index[-1].strftime("%Y/%m/%d")
    vol_part = f"   量 {_fmt_vol_summary(total_vol, quote.is_taiwan)}" if total_vol > 0 else ""
    ref_part = f"   昨收 {prev_close:,.2f}" if prev_close else ""
    fig.text(
        0.04, 0.88,
        f"開 {open_px:,.2f}   高 {high_px:,.2f}   低 {low_px:,.2f}{ref_part}{vol_part}",
        color=MUTED, fontsize=9, ha="left", va="center",
    )
    fig.text(
        0.89, 0.88, f"{day_str} 當日走勢",
        color=MUTED, fontsize=9, ha="right", va="center",
    )
    return _save(fig, path)


def candlestick_chart(
    quote: StockQuote,
    df: pd.DataFrame,
    path: str,
    period_label: str = "日K 近6個月",
    date_fmt: str = "%m/%d",
) -> str:
    """Candlestick chart (daily/weekly/monthly) with volume, price pill, and MA5/20/60."""
    up, down = _colors(quote.is_taiwan)
    mc = mpf.make_marketcolors(
        up=up, down=down, edge="inherit", wick="inherit",
        volume={"up": up, "down": down},
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        facecolor=PANEL,
        figcolor=BG,
        edgecolor=GRID,
        gridcolor=GRID,
        gridstyle="-",
        y_on_right=True,
        rc={
            "axes.labelcolor": MUTED,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "font.sans-serif": plt.rcParams["font.sans-serif"],
            "axes.unicode_minus": False,
        },
    )

    last = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else float(df["Open"].iloc[-1])
    day_chg = last - prev
    day_pct = (day_chg / prev * 100) if prev else 0.0
    day_color = up if day_chg >= 0 else down
    arrow = "▲" if day_chg > 0 else ("▼" if day_chg < 0 else "─")
    sign = "+" if day_chg > 0 else ""

    has_vol = "Volume" in df.columns and float(df["Volume"].fillna(0).sum()) > 0
    if has_vol:
        fig, (ax_price, ax_vol) = plt.subplots(
            2, 1, figsize=(10, 6.4), dpi=150, sharex=True,
            gridspec_kw={"height_ratios": [3.8, 1.1], "hspace": 0.06},
        )
    else:
        fig, ax_price = plt.subplots(figsize=(10, 6.4), dpi=150)
        ax_vol = None

    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(top=0.84, bottom=0.09, left=0.04, right=0.89)
    _style_ax(ax_price, right_axis=True)
    if ax_vol is not None:
        _style_ax(ax_vol, right_axis=True)

    mav_tuple = tuple(w for w in (5, 20, 60) if len(df) >= w)
    mav_colors = [c for w, c in zip((5, 20, 60), (YELLOW, BLUE, PURPLE), strict=False) if len(df) >= w]
    plot_kwargs = dict(
        type="candle",
        ax=ax_price,
        volume=ax_vol if ax_vol is not None else False,
        style=style,
        datetime_format=date_fmt,
        xrotation=0,
        warn_too_much_data=1000,
    )
    if mav_tuple:
        plot_kwargs["mav"] = mav_tuple
        plot_kwargs["mavcolors"] = mav_colors
    mpf.plot(df, **plot_kwargs)

    ax_price.set_ylabel("")
    ax_price.yaxis.set_major_formatter(FuncFormatter(_fmt_price))
    ax_price.tick_params(colors=MUTED, labelsize=8.5, labelbottom=(ax_vol is None))

    # Give vertical headroom for High/Low markers so they never clip
    hi_pos = int(df["High"].values.argmax())
    lo_pos = int(df["Low"].values.argmin())
    hi_val = float(df["High"].iloc[hi_pos])
    lo_val = float(df["Low"].iloc[lo_pos])
    y_pad = max((hi_val - lo_val) * 0.10, hi_val * 0.01, 0.0001)
    ax_price.set_ylim(lo_val - y_pad, hi_val + y_pad)

    _add_price_pill(ax_price, last, day_color)

    ax_price.annotate(
        f"▲ {_fmt_price(hi_val)}",
        xy=(hi_pos, hi_val), xytext=(0, 6), textcoords="offset points",
        color=TEXT, fontsize=8, ha="center", va="bottom",
    )
    ax_price.annotate(
        f"▼ {_fmt_price(lo_val)}",
        xy=(lo_pos, lo_val), xytext=(0, -10), textcoords="offset points",
        color=TEXT, fontsize=8, ha="center", va="top",
    )

    if ax_vol is not None:
        ax_vol.set_ylabel("")
        ax_vol.yaxis.set_major_formatter(_fmt_vol_axis(quote.is_taiwan))
        ax_vol.yaxis.set_major_locator(plt.MaxNLocator(3))
        ax_vol.tick_params(colors=MUTED, labelsize=8.5)

    # Clean two-line header above chart with live MA values (zero overlap!)
    fig.text(
        0.04, 0.935, f"{_short(quote.name)} ({quote.ticker})  {period_label}",
        color=TEXT, fontsize=13.5, fontweight="bold", ha="left", va="center",
    )
    fig.text(
        0.89, 0.935, f"{_price_str(last)}  {arrow} {sign}{_price_str(day_chg)} ({sign}{day_pct:.2f}%)",
        color=day_color, fontsize=13, fontweight="bold", ha="right", va="center",
    )

    close_s = df["Close"]
    ma_items = [
        ("MA5", YELLOW, close_s.rolling(5).mean().iloc[-1] if len(close_s) >= 5 else None),
        ("MA20", BLUE, close_s.rolling(20).mean().iloc[-1] if len(close_s) >= 20 else None),
        ("MA60", PURPLE, close_s.rolling(60).mean().iloc[-1] if len(close_s) >= 60 else None),
    ]
    x_cursor = 0.04
    for label, col, val in ma_items:
        txt = f"● {label} {_fmt_price(float(val))}" if val is not None and pd.notna(val) else f"● {label} --"
        fig.text(x_cursor, 0.88, txt, color=col, fontsize=9, fontweight="bold", ha="left", va="center")
        x_cursor += 0.145

    last_day = df.index[-1].strftime("%Y/%m/%d")
    fig.text(
        0.89, 0.88, f"截至 {last_day}",
        color=MUTED, fontsize=9, ha="right", va="center",
    )
    return _save(fig, path)


def institutional_chart(report: InstReport, path: str) -> str:
    """Grouped bars: daily net buy/sell (in 張 or 億元) per institutional group."""
    days = report.days
    x = list(range(len(days)))
    labels = [d.day.strftime("%m/%d") for d in days]
    scale = report.scale or 1000.0
    unit = report.unit or "張"
    is_amount = unit == "億元"

    def _fmt_u(v: float) -> str:
        return f"{v:+,.1f}" if is_amount else f"{v:+,.0f}"

    groups = [
        ("外資", [d.foreign / scale for d in days], BLUE),
        ("投信", [d.trust / scale for d in days], YELLOW),
        ("自營商", [d.dealer / scale for d in days], PURPLE),
    ]

    fig, ax = plt.subplots(figsize=(10, 6.25), dpi=150)
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(top=0.83, bottom=0.10, left=0.05, right=0.89)
    _style_ax(ax, right_axis=True)

    width = 0.26
    all_vals = [v for _, vals, _ in groups for v in vals]
    max_abs = max((abs(v) for v in all_vals), default=1.0) or 1.0
    ax.set_ylim(-max_abs * 1.22, max_abs * 1.22)

    for i, (label, values, color) in enumerate(groups):
        xs = [xi + (i - 1) * width for xi in x]
        ax.bar(xs, values, width=width, label=label, color=color, alpha=0.92, zorder=3)

        peak_idx = max(range(len(values)), key=lambda k: abs(values[k])) if values else -1
        for k in {len(values) - 1, peak_idx}:
            if 0 <= k < len(values) and abs(values[k]) >= max_abs * 0.06:
                v = values[k]
                va = "bottom" if v >= 0 else "top"
                dy = 4 if v >= 0 else -4
                ax.annotate(
                    _fmt_u(v),
                    xy=(xs[k], v), xytext=(0, dy), textcoords="offset points",
                    color=TEXT, fontsize=7.5, fontweight="bold", ha="center", va=va,
                )

    ax.axhline(0, color=TEXT, linewidth=0.9, alpha=0.65, zorder=4)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color=MUTED, fontsize=8.5)
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _p=None: f"{_fmt_u(v)}{unit}" if v != 0 else "0")
    )

    market_tag = "（上櫃）" if report.market == "TPEX" else ""
    fig.text(
        0.05, 0.935, f"{_short(report.name)} ({report.ticker})  三大法人買賣超{market_tag}",
        color=TEXT, fontsize=14, fontweight="bold", ha="left", va="center",
    )
    if report.foreign_holding_pct is not None:
        fig.text(
            0.89, 0.935, f"外資持股 {report.foreign_holding_pct:.2f}%",
            color=TEXT, fontsize=11, fontweight="bold", ha="right", va="center",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1e293b", edgecolor=GRID),
        )

    sums = [
        ("外資", sum(d.foreign for d in days) / scale, BLUE),
        ("投信", sum(d.trust for d in days) / scale, YELLOW),
        ("自營商", sum(d.dealer for d in days) / scale, PURPLE),
    ]
    x_cursor = 0.05
    for name, total_val, col in sums:
        fig.text(
            x_cursor, 0.875, f"■ {name} {_fmt_u(total_val)}{unit}",
            color=col, fontsize=9.5, fontweight="bold", ha="left", va="center",
        )
        x_cursor += 0.22
    fig.text(
        0.89, 0.875, f"近 {len(days)} 日累計",
        color=MUTED, fontsize=9, ha="right", va="center",
    )
    return _save(fig, path)


def dividend_chart(report: DividendReport, path: str) -> str:
    """Annual dividend bar chart with 5-year average line and value labels."""
    years = report.years
    xs = list(range(len(years)))
    labels = [str(y.year) for y in years]
    vals = [y.amount for y in years]

    fig, ax = plt.subplots(figsize=(10, 6.25), dpi=150)
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(top=0.83, bottom=0.10, left=0.05, right=0.89)
    _style_ax(ax, right_axis=True)

    max_v = max(vals, default=1.0) or 1.0
    ax.set_ylim(0, max_v * 1.24)

    bar_colors = [BLUE if i < len(vals) - 1 else "#f59e0b" for i in range(len(vals))]
    ax.bar(xs, vals, width=0.52, color=bar_colors, alpha=0.92, zorder=3)
    for xi, v in zip(xs, vals, strict=False):
        ax.annotate(
            f"{v:.2f}",
            xy=(xi, v), xytext=(0, 5), textcoords="offset points",
            color=TEXT, fontsize=8.5, fontweight="bold", ha="center", va="bottom",
        )

    if report.avg_5y > 0:
        ax.axhline(report.avg_5y, color="#2ebd85", linewidth=1.1, linestyle="--", alpha=0.8, zorder=4)
        ax.text(
            0.01, report.avg_5y, f" 近5年平均 {report.avg_5y:.2f}",
            transform=ax.get_yaxis_transform(),
            color="#2ebd85", fontsize=8.5, fontweight="bold", va="bottom", ha="left",
            bbox=dict(boxstyle="round,pad=0.16", facecolor=PANEL, edgecolor="none", alpha=0.85),
        )

    ax.set_xticks(xs)
    ax.set_xticklabels(labels, color=MUTED, fontsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p=None: f"{v:.2f}"))

    unit = "元" if report.quote.is_taiwan else report.quote.currency
    fig.text(
        0.05, 0.935, f"{_short(report.quote.name)} ({report.quote.ticker})  歷年股利政策",
        color=TEXT, fontsize=14, fontweight="bold", ha="left", va="center",
    )
    if report.yield_pct is not None:
        fig.text(
            0.89, 0.935, f"預估殖利率 {report.yield_pct:.2f}%",
            color="#2ebd85", fontsize=11, fontweight="bold", ha="right", va="center",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1e293b", edgecolor=GRID),
        )

    ex_str = f"最新除息 {report.latest_ex_date:%Y/%m/%d}" if report.latest_ex_date else ""
    fig.text(
        0.05, 0.875,
        f"最新年度 {report.latest_amount:.2f} {unit}   •   近5年均 {report.avg_5y:.2f} {unit}",
        color=MUTED, fontsize=9.5, ha="left", va="center",
    )
    fig.text(0.89, 0.875, ex_str, color=MUTED, fontsize=9, ha="right", va="center")
    return _save(fig, path)

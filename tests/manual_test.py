"""Manual smoke test: generate all three chart types locally.

Usage: .venv/bin/python tests/manual_test.py
Writes PNGs to test-output/.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.stock import charts, data, institutional  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test-output")
os.makedirs(OUT, exist_ok=True)


def main() -> None:
    for ticker in ["2330", "AAPL"]:
        quote = data.resolve(ticker)
        print(f"resolved {ticker} -> {quote.yahoo_symbol} ({quote.name})")

        df, prev = data.intraday(quote)
        p = charts.intraday_chart(quote, df, prev, os.path.join(OUT, f"P_{ticker}.png"))
        print(f"  intraday: {len(df)} bars -> {p}")

        df = data.daily_history(quote)
        p = charts.candlestick_chart(quote, df, os.path.join(OUT, f"K_{ticker}.png"))
        print(f"  candlestick: {len(df)} bars -> {p}")

    report = institutional.fetch("2330")
    p = charts.institutional_chart(report, os.path.join(OUT, "T_2330.png"))
    print(f"institutional: {len(report.days)} days, "
          f"foreign holding {report.foreign_holding_pct}% -> {p}")


if __name__ == "__main__":
    main()

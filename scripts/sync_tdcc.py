"""Weekly sync script: download TDCC open data CSV and produce compact JSON snapshot.

Usage:
  python scripts/sync_tdcc.py --output tdcc_latest.json [--existing-url https://...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.stock import tdcc  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact TDCC equity dispersion JSON snapshot.")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--existing-url", default="", help="Optional URL of previous snapshot for WoW deltas")
    args = parser.parse_args()

    existing = None
    if args.existing_url:
        try:
            r = requests.get(args.existing_url, headers={"User-Agent": "StockV-TDCC-Sync"}, timeout=15)
            if r.ok:
                existing = r.json()
                print(f"Loaded existing snapshot: date={existing.get('date')} ({len(existing.get('stocks', {}))} stocks)")
        except Exception as exc:
            print(f"No usable existing snapshot ({exc}); creating fresh snapshot")

    print(f"Downloading TDCC CSV from {tdcc.TDCC_CSV_URL} ...")
    resp = requests.get(tdcc.TDCC_CSV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    resp.raise_for_status()
    resp.encoding = "utf-8-sig"

    ymd, stocks = tdcc.parse_tdcc_csv(resp.text)
    if not ymd or not stocks:
        raise RuntimeError("Parsed empty TDCC snapshot; aborting upload")

    snapshot = tdcc.merge_snapshot(ymd, stocks, existing)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, separators=(",", ":"), ensure_ascii=False)

    size_kb = os.path.getsize(args.output) / 1024.0
    print(f"Wrote {args.output}: date={snapshot['date']} prev={snapshot['prev_date'] or 'none'} "
          f"stocks={len(snapshot['stocks'])} size={size_kb:.1f} KB")


if __name__ == "__main__":
    main()

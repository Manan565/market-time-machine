"""Command line entry points: python -m mtm.cli <command>."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from mtm import store
from mtm.ingest import binance, quality
from mtm.ingest.synthetic import generate_range


def _parse_day(text: str) -> date:
    return date.fromisoformat(text)


def cmd_ingest_synth(args: argparse.Namespace) -> None:
    out = Path(args.out)
    frames = generate_range(args.symbols, args.start, args.days, args.trades, seed=args.seed)
    rows = []
    for (symbol, day), df in frames.items():
        store.write_day(df, out, symbol, day)
        rows.append(store.day_summary(df, symbol, day))
        print(f"wrote {symbol} {day} rows={len(df):,}")
    store.update_summary(out, rows)
    print(f"SYNTHETIC data written to {out} (not real market data)")


def cmd_ingest_binance(args: argparse.Namespace) -> None:
    out, raw = Path(args.out), Path(args.raw_dir)
    rows, reports = [], {}
    day = args.start
    while day <= args.end:
        for symbol in args.symbols:
            zip_path = binance.download_day(symbol, day, raw, verify=not args.no_verify)
            cleaned, report = quality.clean(binance.parse_zip(zip_path, symbol))
            store.write_day(cleaned, out, symbol, day)
            rows.append(store.day_summary(cleaned, symbol, day))
            reports[f"{symbol}-{day}"] = report.to_dict()
            print(
                f"{symbol} {day}: {report.n_rows:,} rows, dropped {report.n_dropped_on_clean:,}, "
                f"clean={report.is_clean}"
            )
        day += timedelta(days=1)
    store.update_summary(out, rows)
    (out / "quality_report.json").write_text(json.dumps(reports, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtm")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest-synth", help="write deterministic synthetic ticks to Parquet")
    s.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    s.add_argument("--start", type=_parse_day, default=date(2025, 1, 2))
    s.add_argument("--days", type=int, default=5)
    s.add_argument("--trades", type=int, default=200_000, help="trades per day for the first symbol")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default="data/parquet")
    s.set_defaults(func=cmd_ingest_synth)

    b = sub.add_parser("ingest-binance", help="download, clean and store Binance public trades")
    b.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    b.add_argument("--start", type=_parse_day, required=True)
    b.add_argument("--end", type=_parse_day, required=True)
    b.add_argument("--raw-dir", default="data/raw")
    b.add_argument("--out", default="data/parquet")
    b.add_argument("--no-verify", action="store_true", help="skip checksum verification")
    b.set_defaults(func=cmd_ingest_binance)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

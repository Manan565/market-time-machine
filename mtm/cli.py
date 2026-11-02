"""Command line entry points: python -m mtm.cli <command>."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

from mtm import store
from mtm.engines import ENGINES, assert_results_equal
from mtm.ingest import binance, quality
from mtm.ingest.synthetic import generate_range
from mtm.queries import QUERIES


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


def cmd_demo(args: argparse.Namespace) -> None:
    """Run every query on every engine once, check they agree, and print rough timings.

    This is a smoke test, not the benchmark: one run, no warm-up. The real harness comes in week 2.
    """
    dataset = store.DatasetRef(Path(args.root))
    rows = dataset.row_count(args.symbol)
    print(f"{args.symbol}: {rows:,} rows across {len(dataset.days(args.symbol))} days\n")
    engines = {}
    for name, cls in ENGINES.items():
        engine = cls()
        t0 = time.perf_counter()
        engine.load(dataset, [args.symbol])
        engines[name] = engine
        print(f"{name:>7} load  {time.perf_counter() - t0:8.3f}s")
    print()
    for qid, query in QUERIES.items():
        results, timings = {}, {}
        for name, engine in engines.items():
            t0 = time.perf_counter()
            results[name] = engine.run(qid, {"symbol": args.symbol})
            timings[name] = time.perf_counter() - t0
        names = list(results)
        for other in names[1:]:
            assert_results_equal(results[names[0]], results[other])
        cells = "  ".join(f"{n} {timings[n] * 1000:8.1f}ms" for n in names)
        print(f"{qid} {query.title:<36} rows={len(results[names[0]]):>6,}  {cells}  agree=yes")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from mtm.api.app import create_app

    results = Path(args.results) if args.results else None
    app = create_app(Path(args.root), results, Path(args.web) if args.web else None)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


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

    d = sub.add_parser("demo", help="run Q1-Q3 on every engine, verify agreement, print timings")
    d.add_argument("--root", default="data/parquet")
    d.add_argument("--symbol", default="BTCUSDT")
    d.set_defaults(func=cmd_demo)

    v = sub.add_parser("serve", help="start the API (and the built web app if present)")
    v.add_argument("--root", default="data/parquet")
    v.add_argument("--results", default="bench/results/week2_synthetic.json")
    v.add_argument("--web", default="web/dist")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

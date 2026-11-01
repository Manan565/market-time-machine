"""Benchmark harness: python -m mtm.bench run|worker|plot

Method (also documented in the README):
* Each (engine, dataset size) pair runs in its own subprocess, so peak memory is not polluted by
  other engines or earlier datasets.
* For every query: one cold run (first run in a fresh process; the OS page cache is warm because
  the files were just written or read, so "cold" means cold engine state, not cold disk), then
  N warm runs. We report median and an approximate p95 of the warm runs, never the best run.
* Time includes converting the result to a pandas DataFrame, the same for every engine.
* Peak memory is the process's peak resident set size over load plus all queries.
* After every run the engines' final results are compared with `assert_results_equal`, so a
  fast-but-wrong engine cannot win.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path

import numpy as np
import pandas as pd

from mtm import store
from mtm.engines import ENGINES, assert_results_equal
from mtm.ingest.synthetic import generate_day
from mtm.queries import QUERIES, standard_params

DEFAULT_SIZES = [1_000_000, 10_000_000]
START_DAY = date(2025, 1, 2)
MAX_TRADES_PER_DAY = 2_500_000
EXPLAIN_LIMIT = 6000


# ----------------------------------------------------------------------------- datasets


def ensure_dataset(base: Path, rows: int, seed: int = 7) -> store.DatasetRef:
    """Build (once) a synthetic dataset with exactly `rows` BTCUSDT trades (ETH has 60%)."""
    root = base / f"synth_{rows}"
    if (root / store.SUMMARY_FILE).exists():
        return store.DatasetRef(root)
    n_days = math.ceil(rows / MAX_TRADES_PER_DAY)
    per_day = rows // n_days
    if per_day * n_days != rows:
        raise ValueError(f"rows={rows} must divide evenly into {n_days} days")
    summaries, prices = [], None
    for i in range(n_days):
        day = date.fromordinal(START_DAY.toordinal() + i)
        frames, prices = generate_day(
            ["BTCUSDT", "ETHUSDT"], day, per_day, seed=seed, start_prices=prices
        )
        for symbol, df in frames.items():
            store.write_day(df, root, symbol, day)
            summaries.append(store.day_summary(df, symbol, day))
        del frames
    store.update_summary(root, summaries)
    return store.DatasetRef(root)


# ----------------------------------------------------------------------------- worker


def _proc_status_mb(field: str) -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1]) / 1024
    return float("nan")


def _rss_mb() -> float:
    return _proc_status_mb("VmRSS")


def run_worker(engine_name: str, root: Path, n_warm: int, results_dir: Path) -> dict:
    ref = store.DatasetRef(root)
    day = ref.days("BTCUSDT")[0]
    engine = ENGINES[engine_name]()
    results_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    engine.load(ref, ["BTCUSDT", "ETHUSDT"])
    load_s = time.perf_counter() - t0
    out = {"engine": engine_name, "load_s": load_s, "rss_after_load_mb": _rss_mb(), "queries": {}}

    explain = {}
    for qid in QUERIES:
        params = standard_params(qid, day)
        gc.collect()
        t0 = time.perf_counter()
        result = engine.run(qid, params)
        cold = time.perf_counter() - t0
        warm = []
        for _ in range(n_warm):
            t0 = time.perf_counter()
            result = engine.run(qid, params)
            warm.append(time.perf_counter() - t0)
        result.to_parquet(results_dir / f"{engine_name}_{qid}.parquet", index=False)
        out["queries"][qid] = {
            "cold_s": cold,
            "warm_s": warm,
            "median_s": float(np.median(warm)),
            "p95_s": float(np.percentile(warm, 95)),
            "min_s": float(np.min(warm)),
            "result_rows": len(result),
        }
        if hasattr(engine, "explain"):
            explain[qid] = engine.explain(qid, params)[:EXPLAIN_LIMIT]
    # VmHWM is this process's own high-water mark. ru_maxrss would be wrong here: Linux carries
    # it across exec, so it would report the larger of this worker and its parent.
    out["peak_rss_mb"] = _proc_status_mb("VmHWM")
    if explain:
        out["explain"] = explain
    return out


# ----------------------------------------------------------------------------- orchestrator


def machine_info() -> dict:
    cpu = next(
        (
            ln.split(":", 1)[1].strip()
            for ln in Path("/proc/cpuinfo").read_text().splitlines()
            if ln.startswith("model name")
        ),
        platform.processor(),
    )
    mem_kb = next(
        int(ln.split()[1])
        for ln in Path("/proc/meminfo").read_text().splitlines()
        if ln.startswith("MemTotal")
    )
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "platform": platform.platform(),
        "cpu": cpu,
        "logical_cpus": __import__("os").cpu_count(),
        "ram_gb": round(mem_kb / 1024 / 1024, 1),
        "python": platform.python_version(),
        "packages": {
            p: metadata.version(p) for p in ("pandas", "numpy", "pyarrow", "duckdb")
        },
    }


def check_agreement(results_dir: Path, engine_names: list[str]) -> dict[str, bool]:
    """Compare every engine's final result to the first engine's, query by query."""
    agree = {}
    first = engine_names[0]
    for qid in QUERIES:
        base = pd.read_parquet(results_dir / f"{first}_{qid}.parquet")
        ok = True
        for other in engine_names[1:]:
            try:
                assert_results_equal(base, pd.read_parquet(results_dir / f"{other}_{qid}.parquet"))
            except AssertionError as exc:
                print(f"  DISAGREEMENT {qid} {first} vs {other}: {exc}", file=sys.stderr)
                ok = False
        agree[qid] = ok
    return agree


def run_all(sizes: list[int], engines: list[str], n_warm: int, data_dir: Path, out: Path) -> dict:
    report = {
        "meta": {
            **machine_info(),
            "warm_runs": n_warm,
            "data": "synthetic (see mtm/ingest/synthetic.py); not real market data",
            "duckdb_threads": "default (all logical CPUs)",
            "pandas_threads": "single-threaded",
        },
        "runs": [],
        "agreement": {},
    }
    for rows in sizes:
        # Build in a subprocess so the orchestrator itself stays small and cannot skew anything.
        subprocess.run(
            [sys.executable, "-m", "mtm.bench", "build", "--rows", str(rows),
             "--data-dir", str(data_dir)],
            check=True,
        )  # fmt: skip
        ref = store.DatasetRef(data_dir / f"synth_{rows}")
        print(f"== {rows:,} BTCUSDT rows ({len(ref.days('BTCUSDT'))} days)", flush=True)
        results_dir = data_dir / f"results_{rows}"
        for name in engines:
            tmp = data_dir / f"worker_{name}_{rows}.json"
            cmd = [sys.executable, "-m", "mtm.bench", "worker", "--engine", name, "--root",
                   str(ref.root), "--warm", str(n_warm), "--results", str(results_dir),
                   "--out", str(tmp)]  # fmt: skip
            t0 = time.perf_counter()
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not tmp.exists():
                tail = (proc.stderr or "").strip().splitlines()[-3:]
                print(f"   {name}: FAILED (exit {proc.returncode}) {tail}", flush=True)
                report["runs"].append(
                    {"rows": rows, "engine": name, "failed": f"exit {proc.returncode}: {tail}"}
                )
                continue
            run = json.loads(tmp.read_text())
            run["rows"] = rows
            report["runs"].append(run)
            tmp.unlink()
            print(f"   {name}: {time.perf_counter() - t0:6.1f}s wall, peak {run['peak_rss_mb']:.0f} MB", flush=True)
        done = [r["engine"] for r in report["runs"] if r["rows"] == rows and "failed" not in r]
        if len(done) > 1:
            report["agreement"][str(rows)] = check_agreement(results_dir, done)
            print(f"   engines agree: {report['agreement'][str(rows)]}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(f"wrote {out}")
    return report


# ----------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mtm.bench")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="build datasets, run every engine in a subprocess, compare")
    r.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES)
    r.add_argument("--engines", nargs="+", default=list(ENGINES))
    r.add_argument("--warm", type=int, default=7)
    r.add_argument("--data-dir", default="data/bench")
    r.add_argument("--out", default="bench/results/week2_synthetic.json")

    b = sub.add_parser("build", help="(internal) build one synthetic dataset if missing")
    b.add_argument("--rows", type=int, required=True)
    b.add_argument("--data-dir", default="data/bench")

    w = sub.add_parser("worker", help="(internal) run one engine on one dataset")
    w.add_argument("--engine", required=True)
    w.add_argument("--root", required=True)
    w.add_argument("--warm", type=int, default=7)
    w.add_argument("--results", required=True)
    w.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.command == "run":
        run_all(args.sizes, args.engines, args.warm, Path(args.data_dir), Path(args.out))
    elif args.command == "build":
        ensure_dataset(Path(args.data_dir), args.rows)
    elif args.command == "worker":
        result = run_worker(args.engine, Path(args.root), args.warm, Path(args.results))
        Path(args.out).write_text(json.dumps(result))


if __name__ == "__main__":
    main()

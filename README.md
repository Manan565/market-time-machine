# Market Time Machine

Replay a market day tick by tick, query it with several engines, and see which one wins and why.
The project is a learning and portfolio build around time-series databases: pandas as the baseline,
DuckDB as a modern columnar engine, and (later) Kdb+/Q.

> **Status: week 1 of 5.** Data pipeline, Parquet store, pandas and DuckDB engines for queries
> Q1 to Q6, and cross-engine parity tests. API, replay UI, Sonify, Day Fingerprint, leaderboard and
> Kdb+/Q come in later weeks. The full plan lives in the project design doc.

## Quick start

```bash
make install        # pip install -e ".[dev]"
make test           # parity, ingest and generator tests
make ingest-synth   # write 5 synthetic days of BTCUSDT and ETHUSDT ticks to data/parquet
make demo           # run Q1-Q3 on pandas and DuckDB and print timings
```

## About the data

Real tick data comes from Binance's free public trade files (data.binance.vision). The ingest code
for those files is in `mtm/ingest/binance.py`, with explicit parsing, checksum verification and data
quality checks. **It has been tested against fixture files written to match the documented layout,
not against live downloads** (the development sandbox could not reach the site). Verify the column
layout and timestamp units on a real file the first time you run `make ingest`.

Until then, `make ingest-synth` produces a deterministic synthetic market: volatility regimes,
intraday volume seasonality, and BTC and ETH moving together. It is clearly synthetic and is
never presented as real data. It exists so the engines, tests and benchmarks can be built and
reproduced offline.

## Layout

```text
mtm/
  schema.py          # trade schema shared by every component
  ingest/            # binance parser, quality checks, synthetic generator
  store.py           # partitioned Parquet writer and day summaries
  queries.py         # named benchmark queries (Q1-Q6)
  engines/           # Engine protocol, pandas, DuckDB, pure-Python reference
  cli.py             # ingest and demo commands
tests/               # ingest, generator and engine parity tests
```

## Queries implemented

| ID | Query |
| --- | --- |
| Q1 | VWAP, volume and trade count per N-minute window |
| Q2 | 1-minute OHLC bars |
| Q3 | Rolling 1-hour realized volatility from 1-minute closes |
| Q4 | As-of join: each ETH trade matched to the latest BTC price at or before it (no look-ahead) |
| Q5 | The N largest trades in a time range |
| Q6 | Trade count and volume in a narrow time slice |

### What `make demo` shows today

On 1,000,000 synthetic BTCUSDT ticks every engine agrees on all three queries, and each query takes
roughly 50 to 120 ms in a single, unwarmed run. **That is a smoke test, not a benchmark**, and at this
size neither engine is clearly ahead: DuckDB's ordered `first`/`last` in Q2 and its window query in Q3
were actually slower than pandas in that run. Week 2 adds a proper harness (warm-up, repeated runs,
median and p95, 1M/10M/100M rows, memory) before any claim is made.

Every engine must return the same answer; `tests/test_parity.py` checks pandas and DuckDB against a
slow, obviously correct pure-Python reference.

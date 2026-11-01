"""Named benchmark queries. Every engine must answer each one identically.

Conventions shared by all engines:
* Time buckets are aligned to the Unix epoch (so a 5-minute bucket starts at :00, :05, ...).
* "First" and "last" price in a bucket follow (ts, trade_id) order, never arrival order.
* Time ranges are half-open: start <= ts < end.
* Output frames are sorted as declared in `Query.sort`; empty buckets are simply absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Columns that are integers in every result; everything else is float64 (except `bucket`).
INT_COLUMNS = frozenset({"trades", "trade_id"})


@dataclass(frozen=True)
class Query:
    id: str
    title: str
    description: str
    columns: tuple[str, ...]
    defaults: dict = field(default_factory=dict)
    required: tuple[str, ...] = ()
    sort: tuple[tuple[str, bool], ...] = (("bucket", True),)  # (column, ascending)


QUERIES: dict[str, Query] = {
    "Q1": Query(
        id="Q1",
        title="VWAP per window",
        description=(
            "Volume-weighted average price, total volume and trade count for each N-minute "
            "window. Stresses group-by on time buckets."
        ),
        columns=("bucket", "vwap", "volume", "trades"),
        defaults={"bucket_minutes": 5},
    ),
    "Q2": Query(
        id="Q2",
        title="1-minute OHLC bars",
        description=(
            "Open, high, low, close, volume and trade count per minute. Stresses ordered "
            "first/last aggregation."
        ),
        columns=("bucket", "open", "high", "low", "close", "volume", "trades"),
    ),
    "Q3": Query(
        id="Q3",
        title="Rolling 1-hour realized volatility",
        description=(
            "Square root of the sum of squared 1-minute log returns over the last `window` bars "
            "(rows, not clock time). Defined only once a full window exists. Stresses window "
            "functions."
        ),
        columns=("bucket", "realized_vol"),
        defaults={"window": 60},
    ),
    "Q4": Query(
        id="Q4",
        title="As-of join: price ratio to another symbol",
        description=(
            "For every trade of `symbol`, find the price of the latest `other` trade at or "
            "before it (ties: highest trade_id), then report the mean price ratio and the matched "
            "trade count per minute. Trades before the first `other` trade are dropped. This is "
            "the Kdb+ `aj` operation. Stresses ordered joins and look-ahead correctness."
        ),
        columns=("bucket", "ratio", "trades"),
        defaults={"other": "BTCUSDT"},
    ),
    "Q5": Query(
        id="Q5",
        title="Largest trades in a time range",
        description=(
            "The `n` largest trades by quantity with start <= ts < end (ties: earlier ts, then "
            "lower trade_id). Stresses top-N and time-range filtering."
        ),
        columns=("bucket", "trade_id", "price", "qty"),
        defaults={"n": 10, "start": None, "end": None},
        sort=(("qty", False), ("bucket", True), ("trade_id", True)),
    ),
    "Q6": Query(
        id="Q6",
        title="Narrow time slice",
        description=(
            "Trade count and volume for start <= ts < end, typically seconds wide, on a table "
            "with millions of rows. Stresses range pruning rather than scanning."
        ),
        columns=("bucket", "trades", "volume"),
        required=("start", "end"),
    ),
}


def _ticker(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.isalnum() or not value.isupper():
        raise ValueError(f"params[{name!r}] must be an upper-case alphanumeric ticker like BTCUSDT")
    return value


def resolve(query_id: str, params: dict | None) -> dict:
    """Merge caller params with the query's defaults, validate, and normalise time bounds."""
    if query_id not in QUERIES:
        raise KeyError(f"unknown query {query_id!r}; known: {sorted(QUERIES)}")
    q = QUERIES[query_id]
    merged = {**q.defaults, **(params or {})}
    merged["symbol"] = _ticker(merged.get("symbol"), "symbol")
    if "other" in q.defaults:
        merged["other"] = _ticker(merged["other"], "other")
        if merged["other"] == merged["symbol"]:
            raise ValueError("params['other'] must differ from params['symbol']")
    for key in ("bucket_minutes", "window", "n"):
        if key in merged and (not isinstance(merged[key], int) or merged[key] < 1):
            raise ValueError(f"{key} must be a positive integer")
    for key in q.required:
        if merged.get(key) is None:
            raise ValueError(f"query {query_id} requires params[{key!r}]")
    for key in ("start", "end"):
        if key in merged:
            merged[key] = None if merged[key] is None else pd.Timestamp(merged[key])
    start, end = merged.get("start"), merged.get("end")
    if start is not None and end is not None and end <= start:
        raise ValueError("params['end'] must be after params['start']")
    return merged


def sql_ts(ts: pd.Timestamp) -> str:
    """A TIMESTAMP literal for SQL, from an already-validated pandas Timestamp."""
    return f"TIMESTAMP '{ts.strftime('%Y-%m-%d %H:%M:%S.%f')}'"


def standard_params(query_id: str, day) -> dict:
    """The parameters used for tests and benchmarks, anchored on a dataset's first day.

    Q1-Q3 run on BTCUSDT over everything; Q4 compares ETHUSDT to BTCUSDT; Q5 looks at four busy
    hours of the first day; Q6 looks at a 10 second slice of the US afternoon.
    """
    d = pd.Timestamp(day).strftime("%Y-%m-%d")
    base = {"symbol": "BTCUSDT"}
    return {
        "Q1": base,
        "Q2": base,
        "Q3": base,
        "Q4": {"symbol": "ETHUSDT", "other": "BTCUSDT"},
        "Q5": {**base, "start": f"{d} 10:00:00", "end": f"{d} 14:00:00", "n": 10},
        "Q6": {**base, "start": f"{d} 14:00:00", "end": f"{d} 14:00:10"},
    }[query_id]

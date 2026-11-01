"""Named benchmark queries. Every engine must answer each one identically.

Conventions shared by all engines:
* Time buckets are aligned to the Unix epoch (so a 5-minute bucket starts at :00, :05, ...).
* "First" and "last" price in a bucket follow (ts, trade_id) order, never arrival order.
* Output frames are sorted by `bucket`; empty buckets are simply absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Query:
    id: str
    title: str
    description: str
    columns: tuple[str, ...]
    defaults: dict = field(default_factory=dict)


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
}


def resolve(query_id: str, params: dict | None) -> dict:
    """Merge caller params with the query's defaults and validate them."""
    if query_id not in QUERIES:
        raise KeyError(f"unknown query {query_id!r}; known: {sorted(QUERIES)}")
    merged = {**QUERIES[query_id].defaults, **(params or {})}
    symbol = merged.get("symbol")
    if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.isupper():
        raise ValueError("params['symbol'] must be an upper-case alphanumeric ticker like BTCUSDT")
    for key in ("bucket_minutes", "window"):
        if key in merged and (not isinstance(merged[key], int) or merged[key] < 1):
            raise ValueError(f"{key} must be a positive integer")
    return merged

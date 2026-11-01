"""DuckDB engine: columnar SQL that scans the Parquet files directly."""

from __future__ import annotations

import duckdb
import pandas as pd

from mtm.engines.base import finalize
from mtm.queries import resolve, sql_ts
from mtm.store import DatasetRef

EPOCH = "TIMESTAMP '1970-01-01'"  # pandas floors to the Unix epoch, so DuckDB must too


def _literal(files: list[str]) -> str:
    """Quote self-generated file paths for inlining (CREATE VIEW/TABLE cannot be prepared)."""
    return ", ".join("'" + f.replace("'", "''") + "'" for f in files)


class DuckDBEngine:
    name = "duckdb"

    def __init__(self) -> None:
        self._con = duckdb.connect(":memory:")
        self._views: dict[str, str] = {}

    def load(self, dataset: DatasetRef, symbols: list[str] | None = None) -> None:
        """Register one view per symbol over its Parquet files. No data is read yet."""
        self._views = {}
        for symbol in symbols or dataset.symbols():
            files = [str(p) for p in dataset.files(symbol)]
            if not symbol.isalnum():
                raise ValueError(f"unsafe symbol {symbol!r}")
            view = f"trades_{symbol.lower()}"
            self._con.execute(
                f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet([{_literal(files)}])"
            )
            self._views[symbol] = view

    def sql(self, query_id: str, params: dict | None = None) -> str:
        """The SQL this engine runs for a query (also used by the explain features later)."""
        p = resolve(query_id, params)
        view = self._views[p["symbol"]]
        if query_id == "Q1":
            return (
                f"SELECT time_bucket(INTERVAL '{p['bucket_minutes']} minutes', ts, {EPOCH}) AS bucket, "
                "sum(price * qty) / sum(qty) AS vwap, sum(qty) AS volume, count(*) AS trades "
                f"FROM {view} GROUP BY 1 ORDER BY 1"
            )
        if query_id == "Q2":
            return self._bars_sql(view)
        if query_id == "Q3":
            w = p["window"]
            return (
                f"WITH bars AS ({self._bars_sql(view)}), "
                "r AS (SELECT bucket, ln(close / lag(close) OVER (ORDER BY bucket)) AS r FROM bars), "
                "w AS (SELECT bucket, count(r) OVER win AS n, sum(r * r) OVER win AS ss FROM r "
                f"WINDOW win AS (ORDER BY bucket ROWS BETWEEN {w - 1} PRECEDING AND CURRENT ROW)) "
                f"SELECT bucket, sqrt(ss) AS realized_vol FROM w WHERE n = {w} ORDER BY bucket"
            )
        if query_id == "Q4":
            other = self._views[p["other"]]
            return (
                f"WITH other AS (SELECT ts, last(price ORDER BY trade_id) AS p FROM {other} GROUP BY ts), "
                f"j AS (SELECT l.ts AS ts, l.price / o.p AS ratio FROM {view} l "
                "ASOF JOIN other o ON l.ts >= o.ts) "
                f"SELECT time_bucket(INTERVAL '1 minute', ts, {EPOCH}) AS bucket, "
                "avg(ratio) AS ratio, count(*) AS trades FROM j GROUP BY 1 ORDER BY 1"
            )
        if query_id == "Q5":
            return (
                f"SELECT ts AS bucket, trade_id, price, qty FROM {view} "
                f"WHERE {self._range(p)} ORDER BY qty DESC, ts, trade_id LIMIT {p['n']}"
            )
        if query_id == "Q6":
            return (
                f"SELECT {sql_ts(p['start'])} AS bucket, count(*) AS trades, "
                f"coalesce(sum(qty), 0) AS volume FROM {view} WHERE {self._range(p)}"
            )
        raise NotImplementedError(query_id)

    @staticmethod
    def _range(p: dict) -> str:
        parts = []
        if p.get("start") is not None:
            parts.append(f"ts >= {sql_ts(p['start'])}")
        if p.get("end") is not None:
            parts.append(f"ts < {sql_ts(p['end'])}")
        return " AND ".join(parts) or "true"

    @staticmethod
    def _bars_sql(view: str) -> str:
        return (
            f"SELECT time_bucket(INTERVAL '1 minute', ts, {EPOCH}) AS bucket, "
            "first(price ORDER BY ts, trade_id) AS open, max(price) AS high, min(price) AS low, "
            "last(price ORDER BY ts, trade_id) AS close, sum(qty) AS volume, count(*) AS trades "
            f"FROM {view} GROUP BY 1 ORDER BY 1"
        )

    def run(self, query_id: str, params: dict | None = None) -> pd.DataFrame:
        return finalize(self._con.execute(self.sql(query_id, params)).df(), query_id)

    def explain(self, query_id: str, params: dict | None = None) -> str:
        """DuckDB's EXPLAIN ANALYZE output for a query, for the Engineer explanation mode."""
        rows = self._con.execute("EXPLAIN ANALYZE " + self.sql(query_id, params)).fetchall()
        return "\n".join(str(r[1]) for r in rows)


class DuckDBTableEngine(DuckDBEngine):
    """DuckDB with the data loaded into its own in-memory, time-sorted table.

    This is the like-for-like counterpart of the pandas engine: both pay a one-off load cost and
    then query data that already lives in memory. `DuckDBEngine` instead re-reads Parquet on every
    query, which is a different (and often more convenient) cost model.
    """

    name = "duckdb-mem"

    def load(self, dataset: DatasetRef, symbols: list[str] | None = None) -> None:
        self._views = {}
        for symbol in symbols or dataset.symbols():
            files = [str(p) for p in dataset.files(symbol)]
            if not symbol.isalnum():
                raise ValueError(f"unsafe symbol {symbol!r}")
            table = f"mem_{symbol.lower()}"
            self._con.execute(
                f"CREATE OR REPLACE TABLE {table} AS SELECT ts, trade_id, price, qty "
                f"FROM read_parquet([{_literal(files)}]) ORDER BY ts, trade_id"
            )
            self._views[symbol] = table

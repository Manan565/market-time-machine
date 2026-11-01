"""A slow, obviously correct pure-Python reference. Never used for speed, only for truth.

It uses explicit loops, integer microsecond arithmetic and math.fsum, so a bug here would have to
be a bug in plain Python logic rather than in a library's clever optimisation.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict

import pandas as pd

from mtm.engines.base import finalize
from mtm.queries import resolve
from mtm.store import DatasetRef

US_PER_MINUTE = 60_000_000


class ReferenceEngine:
    name = "reference"

    def __init__(self) -> None:
        self._rows: dict[str, list[tuple]] = {}

    def load(self, dataset: DatasetRef, symbols: list[str] | None = None) -> None:
        self._rows = {}
        for symbol in symbols or dataset.symbols():
            rows: list[tuple] = []
            for path in dataset.files(symbol):
                df = pd.read_parquet(path)
                ts_us = df["ts"].to_numpy().astype("datetime64[us]").astype("int64")
                rows.extend(
                    zip(
                        ts_us.tolist(),
                        df["trade_id"].tolist(),
                        df["price"].tolist(),
                        df["qty"].tolist(),
                        strict=True,
                    )
                )
            rows.sort(key=lambda r: (r[0], r[1]))
            self._rows[symbol] = rows

    def run(self, query_id: str, params: dict | None = None) -> pd.DataFrame:
        p = resolve(query_id, params)
        rows = self._rows[p["symbol"]]
        if query_id == "Q1":
            return finalize(self._q1(rows, p["bucket_minutes"]), "Q1")
        if query_id == "Q2":
            return finalize(self._bars(rows), "Q2")
        if query_id == "Q3":
            return finalize(self._q3(rows, p["window"]), "Q3")
        if query_id == "Q4":
            return finalize(self._q4(rows, self._rows[p["other"]]), "Q4")
        if query_id == "Q5":
            return finalize(self._q5(rows, p["start"], p["end"], p["n"]), "Q5")
        if query_id == "Q6":
            return finalize(self._q6(rows, p["start"], p["end"]), "Q6")
        raise NotImplementedError(query_id)

    @staticmethod
    def _in_range(rows: list[tuple], start, end) -> list[tuple]:
        lo = None if start is None else start.value // 1000
        hi = None if end is None else end.value // 1000
        return [r for r in rows if (lo is None or r[0] >= lo) and (hi is None or r[0] < hi)]

    def _q4(self, rows: list[tuple], other_rows: list[tuple]) -> pd.DataFrame:
        # latest other-price per timestamp: rows are in (ts, trade_id) order, so the last wins
        latest: dict[int, float] = {}
        for ts, _tid, price, _qty in other_rows:
            latest[ts] = price
        other_ts = sorted(latest)
        groups: dict[int, list[float]] = defaultdict(list)
        for ts, _tid, price, _qty in rows:
            i = bisect_right(other_ts, ts) - 1  # latest other trade with ts' <= ts
            if i >= 0:
                groups[ts // US_PER_MINUTE * US_PER_MINUTE].append(price / latest[other_ts[i]])
        out = [
            (self._to_ts(b), math.fsum(v) / len(v), len(v)) for b, v in sorted(groups.items())
        ]
        return pd.DataFrame(out, columns=["bucket", "ratio", "trades"])

    def _q5(self, rows: list[tuple], start, end, n: int) -> pd.DataFrame:
        picked = sorted(self._in_range(rows, start, end), key=lambda r: (-r[3], r[0], r[1]))[:n]
        out = [(self._to_ts(ts), tid, price, qty) for ts, tid, price, qty in picked]
        return pd.DataFrame(out, columns=["bucket", "trade_id", "price", "qty"])

    def _q6(self, rows: list[tuple], start, end) -> pd.DataFrame:
        picked = self._in_range(rows, start, end)
        volume = math.fsum(r[3] for r in picked)
        return pd.DataFrame({"bucket": [start], "trades": [len(picked)], "volume": [volume]})

    @staticmethod
    def _to_ts(bucket_us: int) -> pd.Timestamp:
        return pd.Timestamp(bucket_us, unit="us")

    def _q1(self, rows: list[tuple], minutes: int) -> pd.DataFrame:
        width = minutes * US_PER_MINUTE
        groups: dict[int, list[tuple]] = defaultdict(list)
        for ts, _tid, price, qty in rows:
            groups[ts // width * width].append((price, qty))
        out = []
        for bucket in sorted(groups):
            trades = groups[bucket]
            volume = math.fsum(q for _, q in trades)
            pv = math.fsum(p * q for p, q in trades)
            out.append((self._to_ts(bucket), pv / volume, volume, len(trades)))
        return pd.DataFrame(out, columns=["bucket", "vwap", "volume", "trades"])

    def _bars(self, rows: list[tuple]) -> pd.DataFrame:
        groups: dict[int, list[tuple]] = defaultdict(list)
        for ts, _tid, price, qty in rows:  # rows are already in (ts, trade_id) order
            groups[ts // US_PER_MINUTE * US_PER_MINUTE].append((price, qty))
        out = []
        for bucket in sorted(groups):
            trades = groups[bucket]
            prices = [p for p, _ in trades]
            out.append(
                (
                    self._to_ts(bucket),
                    prices[0],
                    max(prices),
                    min(prices),
                    prices[-1],
                    math.fsum(q for _, q in trades),
                    len(trades),
                )
            )
        cols = ["bucket", "open", "high", "low", "close", "volume", "trades"]
        return pd.DataFrame(out, columns=cols)

    def _q3(self, rows: list[tuple], window: int) -> pd.DataFrame:
        bars = self._bars(rows)
        closes = bars["close"].tolist()
        squares = [None] + [math.log(closes[i] / closes[i - 1]) ** 2 for i in range(1, len(closes))]
        out = []
        for i in range(len(closes)):
            chunk = squares[max(0, i - window + 1) : i + 1]
            valid = [s for s in chunk if s is not None]
            if len(valid) == window:
                out.append((bars["bucket"].iloc[i], math.sqrt(math.fsum(valid))))
        return pd.DataFrame(out, columns=["bucket", "realized_vol"])

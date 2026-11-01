"""pandas engine: the in-memory baseline, written the way a careful pandas user would.

Fairness choices (also stated in the README):
* Vectorized operations only, no Python loops over rows.
* Only the four columns the queries need are read from Parquet.
* Data is known to be sorted by (ts, trade_id), so time-range queries (Q5, Q6) use binary search
  (`searchsorted`) instead of a full boolean-mask scan.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from mtm.engines.base import finalize
from mtm.queries import resolve
from mtm.store import DatasetRef

COLUMNS = ["ts", "trade_id", "price", "qty"]


class PandasEngine:
    name = "pandas"

    def __init__(self) -> None:
        self._frames: dict[str, pd.DataFrame] = {}

    def load(self, dataset: DatasetRef, symbols: list[str] | None = None) -> None:
        """Read every file for each symbol into memory (this is pandas' real cost model)."""
        self._frames = {}
        for symbol in symbols or dataset.symbols():
            parts = [pd.read_parquet(p, columns=COLUMNS) for p in dataset.files(symbol)]
            df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
            if len(df) and not df["ts"].is_monotonic_increasing:
                df = df.sort_values(["ts", "trade_id"], kind="stable").reset_index(drop=True)
            self._frames[symbol] = df

    def run(self, query_id: str, params: dict | None = None) -> pd.DataFrame:
        p = resolve(query_id, params)
        df = self._frames[p["symbol"]]
        if query_id == "Q1":
            out = self._q1(df, p["bucket_minutes"])
        elif query_id == "Q2":
            out = self._bars(df)
        elif query_id == "Q3":
            out = self._q3(df, p["window"])
        elif query_id == "Q4":
            out = self._q4(df, self._frames[p["other"]])
        elif query_id == "Q5":
            out = self._q5(df, p["start"], p["end"], p["n"])
        elif query_id == "Q6":
            out = self._q6(df, p["start"], p["end"])
        else:
            raise NotImplementedError(query_id)
        return finalize(out, query_id)

    @staticmethod
    def _q1(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
        bucket = df["ts"].dt.floor(f"{minutes}min")
        work = pd.DataFrame({"bucket": bucket, "pv": df["price"] * df["qty"], "qty": df["qty"]})
        g = work.groupby("bucket", sort=True)
        out = g.agg(pv=("pv", "sum"), volume=("qty", "sum"), trades=("qty", "size")).reset_index()
        out["vwap"] = out["pv"] / out["volume"]
        return out

    @staticmethod
    def _bars(df: pd.DataFrame) -> pd.DataFrame:
        work = pd.DataFrame(
            {"bucket": df["ts"].dt.floor("1min"), "price": df["price"], "qty": df["qty"]}
        )
        g = work.groupby("bucket", sort=True)
        return g.agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("qty", "sum"),
            trades=("qty", "size"),
        ).reset_index()

    def _q3(self, df: pd.DataFrame, window: int) -> pd.DataFrame:
        bars = self._bars(df)
        r = np.log(bars["close"]).diff()
        rv = np.sqrt((r * r).rolling(window, min_periods=window).sum())
        out = pd.DataFrame({"bucket": bars["bucket"], "realized_vol": rv})
        return out.dropna().reset_index(drop=True)

    @staticmethod
    def _q4(left: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
        # Both frames are sorted by (ts, trade_id), so keeping the last row per ts keeps the
        # highest trade_id. merge_asof(backward) then takes the latest other-price at or before ts.
        right = other[["ts", "price"]].drop_duplicates("ts", keep="last")
        right = right.rename(columns={"price": "other_price"})
        joined = pd.merge_asof(left[["ts", "price"]], right, on="ts", direction="backward")
        joined = joined.dropna(subset=["other_price"])
        work = pd.DataFrame(
            {"bucket": joined["ts"].dt.floor("1min"), "ratio": joined["price"] / joined["other_price"]}
        )
        g = work.groupby("bucket", sort=True)
        return g.agg(ratio=("ratio", "mean"), trades=("ratio", "size")).reset_index()

    @staticmethod
    def _bounds(df: pd.DataFrame, start, end) -> tuple[int, int]:
        ts = df["ts"]
        lo = 0 if start is None else int(ts.searchsorted(start, side="left"))
        hi = len(df) if end is None else int(ts.searchsorted(end, side="left"))
        return lo, max(lo, hi)

    def _q5(self, df: pd.DataFrame, start, end, n: int) -> pd.DataFrame:
        lo, hi = self._bounds(df, start, end)
        sub = df.iloc[lo:hi]
        # keep="all" retains every trade tied with the n-th largest, so the tie-break below is exact
        top = sub.nlargest(n, "qty", keep="all")
        top = top.sort_values(["qty", "ts", "trade_id"], ascending=[False, True, True]).head(n)
        return top.rename(columns={"ts": "bucket"})

    def _q6(self, df: pd.DataFrame, start, end) -> pd.DataFrame:
        lo, hi = self._bounds(df, start, end)
        volume = float(df["qty"].iloc[lo:hi].sum())
        return pd.DataFrame({"bucket": [start], "trades": [hi - lo], "volume": [volume]})

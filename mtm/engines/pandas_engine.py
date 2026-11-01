"""pandas engine: the in-memory baseline. Vectorized, no Python loops over rows."""

from __future__ import annotations

import numpy as np
import pandas as pd

from mtm.engines.base import finalize
from mtm.queries import resolve
from mtm.store import DatasetRef


class PandasEngine:
    name = "pandas"

    def __init__(self) -> None:
        self._frames: dict[str, pd.DataFrame] = {}

    def load(self, dataset: DatasetRef, symbols: list[str] | None = None) -> None:
        """Read every file for each symbol into memory (this is pandas' real cost model)."""
        self._frames = {}
        for symbol in symbols or dataset.symbols():
            parts = [pd.read_parquet(p) for p in dataset.files(symbol)]
            df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            if len(df) and not df["ts"].is_monotonic_increasing:
                df = df.sort_values(["ts", "trade_id"], kind="stable").reset_index(drop=True)
            self._frames[symbol] = df

    def run(self, query_id: str, params: dict | None = None) -> pd.DataFrame:
        p = resolve(query_id, params)
        df = self._frames[p["symbol"]]
        if query_id == "Q1":
            return finalize(self._q1(df, p["bucket_minutes"]), "Q1")
        if query_id == "Q2":
            return finalize(self._bars(df), "Q2")
        if query_id == "Q3":
            return finalize(self._q3(df, p["window"]), "Q3")
        raise NotImplementedError(query_id)

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
            {
                "bucket": df["ts"].dt.floor("1min"),
                "price": df["price"],
                "qty": df["qty"],
            }
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

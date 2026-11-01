"""Data-quality checks. Problems are counted and reported, never silently fixed."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class QualityReport:
    n_rows: int
    n_duplicate_rows: int
    n_duplicate_trade_ids: int
    n_nonpositive_price: int
    n_nonpositive_qty: int
    n_backwards_ts: int
    max_gap_seconds: float
    n_gaps_over_threshold: int
    gap_threshold_seconds: float

    @property
    def n_dropped_on_clean(self) -> int:
        return self.n_duplicate_rows + self.n_nonpositive_price + self.n_nonpositive_qty

    @property
    def is_clean(self) -> bool:
        return (
            self.n_duplicate_rows == 0
            and self.n_duplicate_trade_ids == 0
            and self.n_nonpositive_price == 0
            and self.n_nonpositive_qty == 0
            and self.n_backwards_ts == 0
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_clean"] = self.is_clean
        return d


def check(df: pd.DataFrame, gap_threshold_seconds: float = 300.0) -> QualityReport:
    """Inspect a trades frame in the order it arrived (do not sort first)."""
    n = len(df)
    if n == 0:
        return QualityReport(0, 0, 0, 0, 0, 0, 0.0, 0, gap_threshold_seconds)
    ts = df["ts"]
    deltas = ts.diff().dt.total_seconds().dropna()
    return QualityReport(
        n_rows=n,
        n_duplicate_rows=int(df.duplicated().sum()),
        n_duplicate_trade_ids=int(df["trade_id"].duplicated().sum()),
        n_nonpositive_price=int((df["price"] <= 0).sum()),
        n_nonpositive_qty=int((df["qty"] <= 0).sum()),
        n_backwards_ts=int((deltas < 0).sum()),
        max_gap_seconds=float(deltas.max()) if len(deltas) else 0.0,
        n_gaps_over_threshold=int((deltas > gap_threshold_seconds).sum()),
        gap_threshold_seconds=gap_threshold_seconds,
    )


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, QualityReport]:
    """Drop exact duplicates and non-positive price or quantity, then sort.

    Returns the cleaned frame and the report describing what was found before cleaning.
    """
    report = check(df)
    out = df.drop_duplicates()
    out = out[(out["price"] > 0) & (out["qty"] > 0)]
    out = out.sort_values(["ts", "trade_id"], kind="stable").reset_index(drop=True)
    return out, report

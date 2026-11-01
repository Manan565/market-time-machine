"""The Engine protocol and helpers for comparing engine output."""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd

from mtm.queries import INT_COLUMNS, QUERIES
from mtm.store import DatasetRef


class Engine(Protocol):
    name: str

    def load(self, dataset: DatasetRef, symbols: list[str] | None = None) -> None:
        """Prepare the engine to query `symbols` (all symbols when None)."""

    def run(self, query_id: str, params: dict | None = None) -> pd.DataFrame:
        """Run a named query and return a frame with the query's declared columns."""


def finalize(df: pd.DataFrame, query_id: str) -> pd.DataFrame:
    """Coerce an engine's raw result to the canonical dtypes and ordering."""
    query = QUERIES[query_id]
    cols = list(query.columns)
    out = df[cols].copy()
    out["bucket"] = pd.to_datetime(out["bucket"]).astype("datetime64[us]")
    for col in cols[1:]:
        out[col] = out[col].astype("int64" if col in INT_COLUMNS else "float64")
    by, ascending = zip(*query.sort, strict=True)
    return out.sort_values(list(by), ascending=list(ascending), kind="stable").reset_index(
        drop=True
    )


def assert_results_equal(a: pd.DataFrame, b: pd.DataFrame, rtol: float = 1e-8, atol: float = 1e-12):
    """Raise AssertionError unless two query results agree.

    Buckets and integer columns must match exactly. Floats may differ only by summation-order noise.
    """
    assert list(a.columns) == list(b.columns), f"columns differ: {list(a.columns)} vs {list(b.columns)}"
    assert len(a) == len(b), f"row counts differ: {len(a)} vs {len(b)}"
    if len(a) == 0:
        return
    assert (a["bucket"].to_numpy() == b["bucket"].to_numpy()).all(), "bucket values differ"
    for col in a.columns[1:]:
        x, y = a[col].to_numpy(), b[col].to_numpy()
        if col in INT_COLUMNS:
            assert (x == y).all(), f"{col} differs"
        else:
            bad = ~np.isclose(x, y, rtol=rtol, atol=atol)
            assert not bad.any(), (
                f"column {col!r} differs at {int(bad.sum())} rows, first {np.flatnonzero(bad)[:3]}"
            )

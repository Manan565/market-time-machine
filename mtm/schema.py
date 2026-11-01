"""The one trade schema every component shares."""

from __future__ import annotations

import pandas as pd

# trade_id breaks ties between trades with identical timestamps, so "first" and "last" are
# deterministic in every engine. Without it, engines could legitimately disagree.
TRADE_COLUMNS = ["ts", "symbol", "trade_id", "price", "qty", "is_buyer_maker"]

DTYPES = {
    "symbol": "string",
    "trade_id": "int64",
    "price": "float64",
    "qty": "float64",
    "is_buyer_maker": "bool",
}


def empty_trades() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "ts": pd.Series([], dtype="datetime64[us]"),
            "symbol": pd.Series([], dtype="string"),
            "trade_id": pd.Series([], dtype="int64"),
            "price": pd.Series([], dtype="float64"),
            "qty": pd.Series([], dtype="float64"),
            "is_buyer_maker": pd.Series([], dtype="bool"),
        }
    )
    return df[TRADE_COLUMNS]


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with the canonical columns, dtypes and (ts, trade_id) ordering."""
    missing = [c for c in TRADE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    out = df[TRADE_COLUMNS].copy()
    out["ts"] = out["ts"].astype("datetime64[us]")
    for col, dtype in DTYPES.items():
        out[col] = out[col].astype(dtype)
    return out.sort_values(["ts", "trade_id"], kind="stable").reset_index(drop=True)

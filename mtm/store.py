"""Partitioned Parquet store: <root>/symbol=<SYM>/date=<YYYY-MM-DD>/part-0.parquet.

Files are sorted by (ts, trade_id), ZSTD compressed, with modest row groups so engines can skip
row groups using min/max statistics on `ts`. Every file keeps its `symbol` column so a single file
is self-describing; engines choose files by path rather than relying on hive partitioning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from mtm.schema import TRADE_COLUMNS, normalize

ROW_GROUP_SIZE = 131_072
SUMMARY_FILE = "summary.parquet"

_ARROW_SCHEMA = pa.schema(
    [
        ("ts", pa.timestamp("us")),
        ("symbol", pa.string()),
        ("trade_id", pa.int64()),
        ("price", pa.float64()),
        ("qty", pa.float64()),
        ("is_buyer_maker", pa.bool_()),
    ]
)


def day_path(root: Path, symbol: str, day: date) -> Path:
    return root / f"symbol={symbol}" / f"date={day.isoformat()}" / "part-0.parquet"


def write_day(df: pd.DataFrame, root: Path, symbol: str, day: date) -> Path:
    path = day_path(root, symbol, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(normalize(df), schema=_ARROW_SCHEMA, preserve_index=False)
    pq.write_table(table, path, compression="zstd", row_group_size=ROW_GROUP_SIZE)
    return path


def day_summary(df: pd.DataFrame, symbol: str, day: date) -> dict:
    """One row of per-day statistics. is_buyer_maker=True means the seller was the aggressor."""
    df = normalize(df)
    sell = df.loc[df["is_buyer_maker"], "qty"].sum()
    buy = df.loc[~df["is_buyer_maker"], "qty"].sum()
    first, last = df["price"].iloc[0], df["price"].iloc[-1]
    high, low = df["price"].max(), df["price"].min()
    return {
        "symbol": symbol,
        "date": pd.Timestamp(day),
        "open": float(first),
        "high": float(high),
        "low": float(low),
        "close": float(last),
        "volume": float(df["qty"].sum()),
        "buy_volume": float(buy),
        "sell_volume": float(sell),
        "n_trades": len(df),
        "range_pct": float((high - low) / first),
    }


def update_summary(root: Path, rows: list[dict]) -> pd.DataFrame:
    """Merge new summary rows into root/summary.parquet (latest row wins per symbol and date)."""
    path = root / SUMMARY_FILE
    new = pd.DataFrame(rows)
    if path.exists():
        old = pd.read_parquet(path)
        new = pd.concat([old, new], ignore_index=True)
    new = new.drop_duplicates(["symbol", "date"], keep="last").sort_values(["symbol", "date"])
    new = new.reset_index(drop=True)
    root.mkdir(parents=True, exist_ok=True)
    new.to_parquet(path, index=False, compression="zstd")
    return new


@dataclass(frozen=True)
class DatasetRef:
    """A pointer to a Parquet store, with helpers to list what is in it."""

    root: Path

    def files(self, symbol: str, start: date | None = None, end: date | None = None) -> list[Path]:
        out = []
        for p in sorted((self.root / f"symbol={symbol}").glob("date=*/part-0.parquet")):
            d = date.fromisoformat(p.parent.name.split("=", 1)[1])
            if (start is None or d >= start) and (end is None or d <= end):
                out.append(p)
        return out

    def symbols(self) -> list[str]:
        return sorted(p.name.split("=", 1)[1] for p in self.root.glob("symbol=*"))

    def days(self, symbol: str) -> list[date]:
        return [date.fromisoformat(p.parent.name.split("=", 1)[1]) for p in self.files(symbol)]

    def summary(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / SUMMARY_FILE)

    def row_count(self, symbol: str) -> int:
        return sum(pq.ParquetFile(p).metadata.num_rows for p in self.files(symbol))


__all__ = ["TRADE_COLUMNS", "DatasetRef", "day_path", "day_summary", "update_summary", "write_day"]

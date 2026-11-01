"""Download and parse Binance public spot trade files (data.binance.vision).

Layout assumed (verify on a real file the first time you run this):
  URL    https://data.binance.vision/data/spot/daily/trades/<SYMBOL>/<SYMBOL>-trades-<YYYY-MM-DD>.zip
  Checksum  the same URL with `.CHECKSUM` appended (sha256, "<hash>  <filename>")
  CSV columns, no header in most files:
    id, price, qty, quote_qty, time, is_buyer_maker, is_best_match
  `time` is epoch milliseconds in older files and epoch microseconds in newer ones, so the unit is
  detected from magnitude rather than assumed.
"""

from __future__ import annotations

import hashlib
import io
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from mtm.schema import TRADE_COLUMNS

BASE_URL = "https://data.binance.vision/data/spot/daily/trades"
RAW_COLUMNS = ["id", "price", "qty", "quote_qty", "time", "is_buyer_maker", "is_best_match"]


def day_url(symbol: str, day: date) -> str:
    return f"{BASE_URL}/{symbol}/{symbol}-trades-{day.isoformat()}.zip"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, dest: Path, timeout: float = 60.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def download_day(symbol: str, day: date, dest_dir: Path, verify: bool = True) -> Path:
    """Download one day's zip (and verify its checksum). Returns the zip path."""
    url = day_url(symbol, day)
    zip_path = dest_dir / Path(url).name
    if not zip_path.exists():
        _fetch(url, zip_path)
    if verify:
        checksum_path = zip_path.with_suffix(zip_path.suffix + ".CHECKSUM")
        _fetch(url + ".CHECKSUM", checksum_path)
        expected = checksum_path.read_text().split()[0].lower()
        actual = sha256_of(zip_path)
        if expected != actual:
            raise ValueError(f"checksum mismatch for {zip_path.name}: {expected} != {actual}")
    return zip_path


def epoch_to_ts(values: pd.Series) -> pd.Series:
    """Convert epoch ms or us to datetime64[us], detecting the unit from magnitude."""
    if values.empty:
        return pd.Series([], dtype="datetime64[us]")
    peak = int(values.max())
    # Epoch ms is ~1e12 for today's dates and epoch us is ~1e15; 1e14 separates them cleanly.
    unit = "us" if peak > 10**14 else "ms"
    return pd.to_datetime(values.astype("int64"), unit=unit).astype("datetime64[us]")


def parse_csv(source, symbol: str) -> pd.DataFrame:
    """Parse a Binance trades CSV (path or file-like) into the canonical trade frame.

    The frame keeps the file's original row order so quality checks can see problems; callers
    run `quality.clean` before storing. A header row, if present, is detected and dropped.
    """
    raw = pd.read_csv(source, header=None, names=RAW_COLUMNS)
    if not pd.api.types.is_integer_dtype(raw["id"]):  # a header row forced strings
        raw = raw.iloc[1:]
    raw = raw.astype({"id": "int64", "price": "float64", "qty": "float64", "time": "int64"})
    maker = raw["is_buyer_maker"].astype(str).str.lower().eq("true")
    out = pd.DataFrame(
        {
            "ts": epoch_to_ts(raw["time"].reset_index(drop=True)),
            "symbol": symbol,
            "trade_id": raw["id"].reset_index(drop=True),
            "price": raw["price"].reset_index(drop=True),
            "qty": raw["qty"].reset_index(drop=True),
            "is_buyer_maker": maker.reset_index(drop=True).astype(bool),
        }
    )
    return out[TRADE_COLUMNS]


def parse_zip(zip_path: Path, symbol: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(name) as f:
            return parse_csv(io.TextIOWrapper(f, encoding="utf-8"), symbol)

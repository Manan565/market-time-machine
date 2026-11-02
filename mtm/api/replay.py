"""Replay core: turn one day of ticks into frames and bars. No web framework in here.

A *frame* summarises every trade in a half-open simulated-time window [start_s, end_s). The
websocket layer sends one frame per ~50 ms of wall time, so a 1000x replay sends frames that each
cover 50 simulated seconds. Frames carry exactly what the browser needs to draw a live price,
cumulative VWAP and (in week 4) the Sonify audio: last price, volume, buy/sell split, largest trade.

All aggregation is O(log n) per frame: trades are sorted by time, so window edges come from binary
search and totals from prefix sums.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from mtm import store

DAY_SECONDS = 86_400
MAX_SPEED = 20_000.0
MIN_SPEED = 1.0


@dataclass
class DayData:
    symbol: str
    day: date
    t0_us: int  # midnight UTC of `day`, microseconds since the Unix epoch
    ts_us: np.ndarray
    price: np.ndarray
    qty: np.ndarray
    sell: np.ndarray  # is_buyer_maker: True means the seller was the aggressor
    cum_qty: np.ndarray = field(repr=False)  # prefix sums, length n + 1, leading zero
    cum_pv: np.ndarray = field(repr=False)
    cum_sell: np.ndarray = field(repr=False)

    @classmethod
    def load(cls, root: Path, symbol: str, day: date) -> DayData:
        path = store.day_path(root, symbol, day)
        if not path.exists():
            raise FileNotFoundError(f"no data for {symbol} on {day}")
        df = pd.read_parquet(path, columns=["ts", "price", "qty", "is_buyer_maker"])
        ts_us = df["ts"].to_numpy().astype("datetime64[us]").astype("int64")
        price = df["price"].to_numpy(dtype="float64")
        qty = df["qty"].to_numpy(dtype="float64")
        sell = df["is_buyer_maker"].to_numpy(dtype="bool")
        zero = np.zeros(1)
        return cls(
            symbol=symbol,
            day=day,
            t0_us=int(pd.Timestamp(day).value // 1000),
            ts_us=ts_us,
            price=price,
            qty=qty,
            sell=sell,
            cum_qty=np.concatenate([zero, np.cumsum(qty)]),
            cum_pv=np.concatenate([zero, np.cumsum(price * qty)]),
            cum_sell=np.concatenate([zero, np.cumsum(np.where(sell, qty, 0.0))]),
        )

    def __len__(self) -> int:
        return len(self.ts_us)

    def index(self, offset_s: float) -> int:
        """Number of trades strictly before `offset_s` seconds after midnight."""
        edge = self.t0_us + round(offset_s * 1_000_000)
        return int(np.searchsorted(self.ts_us, edge, side="left"))

    def _iso(self, offset_s: float) -> str:
        return (pd.Timestamp(self.t0_us, unit="us") + pd.Timedelta(seconds=offset_s)).isoformat()

    def frame(self, start_s: float, end_s: float) -> dict:
        """Aggregate trades with start_s <= t < end_s, plus running totals up to end_s."""
        lo, hi = self.index(start_s), self.index(end_s)
        hi = max(lo, hi)
        n = hi - lo
        total = hi  # trades so far
        last_price = float(self.price[hi - 1]) if hi > 0 else None
        volume = float(self.cum_qty[hi] - self.cum_qty[lo])
        sell_volume = float(self.cum_sell[hi] - self.cum_sell[lo])
        out = {
            "offset_s": end_s,
            "ts": self._iso(end_s),
            "n": n,
            "last": last_price,
            "open": float(self.price[lo]) if n else last_price,
            "high": float(self.price[lo:hi].max()) if n else last_price,
            "low": float(self.price[lo:hi].min()) if n else last_price,
            "volume": volume,
            "sell_volume": sell_volume,
            "buy_volume": volume - sell_volume,
            "max_qty": float(self.qty[lo:hi].max()) if n else 0.0,
            "cum_trades": total,
            "cum_volume": float(self.cum_qty[hi]),
            "cum_vwap": float(self.cum_pv[hi] / self.cum_qty[hi]) if hi > 0 else None,
        }
        return out

    def bars(self, minutes: int = 1) -> dict:
        """Fixed-width bars across the whole day, for the chart's background line.

        Empty bars carry the previous close forward (flat); bars before the first trade use the
        day's first price. `cum_vwap` is the running VWAP at the end of each bar.
        """
        if self.__len__() == 0:
            return {"t": [], "close": [], "high": [], "low": [], "volume": [], "cum_vwap": []}
        width = minutes * 60
        edges_s = np.arange(0, DAY_SECONDS + 1, width, dtype="float64")
        idx = np.searchsorted(self.ts_us, self.t0_us + (edges_s * 1_000_000).astype("int64"), "left")
        lo, hi = idx[:-1], idx[1:]
        nonempty = hi > lo
        close = np.where(nonempty, self.price[np.maximum(hi - 1, 0)], np.nan)
        close = pd.Series(close).ffill().bfill().to_numpy()
        high = np.full(len(lo), np.nan)
        low = np.full(len(lo), np.nan)
        for i in np.flatnonzero(nonempty):
            seg = self.price[lo[i] : hi[i]]
            high[i], low[i] = seg.max(), seg.min()
        high = np.where(nonempty, high, close)
        low = np.where(nonempty, low, close)
        volume = self.cum_qty[hi] - self.cum_qty[lo]
        with np.errstate(invalid="ignore", divide="ignore"):
            vwap = np.where(self.cum_qty[hi] > 0, self.cum_pv[hi] / self.cum_qty[hi], np.nan)
        vwap = pd.Series(vwap).ffill().bfill().to_numpy()
        t_ms = (self.t0_us // 1000 + (edges_s[:-1] * 1000).astype("int64")).tolist()
        return {
            "t": t_ms,
            "close": close.tolist(),
            "high": high.tolist(),
            "low": low.tolist(),
            "volume": volume.tolist(),
            "cum_vwap": vwap.tolist(),
        }

    def summary(self) -> dict:
        n = len(self)
        return {
            "symbol": self.symbol,
            "date": self.day.isoformat(),
            "n_trades": n,
            "open": float(self.price[0]) if n else None,
            "t0_ms": self.t0_us // 1000,
            "day_seconds": DAY_SECONDS,
        }


class DayCache:
    """A small LRU cache of loaded days; each day is a few tens of MB at most."""

    def __init__(self, root: Path, max_days: int = 8) -> None:
        self.root, self.max_days = root, max_days
        self._items: OrderedDict[tuple[str, date], DayData] = OrderedDict()

    def get(self, symbol: str, day: date) -> DayData:
        key = (symbol, day)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        data = DayData.load(self.root, symbol, day)
        self._items[key] = data
        while len(self._items) > self.max_days:
            self._items.popitem(last=False)
        return data


@dataclass
class ReplaySession:
    """Playback state for one connected client. Pure logic; the websocket just drives it."""

    data: DayData
    speed: float = 600.0
    pos_s: float = 0.0
    playing: bool = False

    def set_speed(self, speed: float) -> None:
        self.speed = float(min(MAX_SPEED, max(MIN_SPEED, speed)))

    def seek(self, offset_s: float) -> dict:
        self.pos_s = float(min(DAY_SECONDS, max(0.0, offset_s)))
        return self.data.frame(self.pos_s, self.pos_s)  # empty window: just the running totals

    def step(self, wall_dt: float) -> dict | None:
        """Advance by `wall_dt` seconds of wall time. Returns None once the day has ended."""
        if self.pos_s >= DAY_SECONDS:
            self.playing = False
            return None
        start = self.pos_s
        end = min(DAY_SECONDS, start + self.speed * wall_dt)
        self.pos_s = end
        return self.data.frame(start, end)

    @property
    def finished(self) -> bool:
        return self.pos_s >= DAY_SECONDS

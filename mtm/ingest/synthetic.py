"""Deterministic synthetic tick data. This is NOT real market data.

It exists so the engines, tests and benchmarks can be built and reproduced offline. The generator
bakes in the features that make the queries interesting:

* a shared market factor, so BTC and ETH move together (needed for the as-of join later)
* slowly varying volatility with one shock event per day (a fast move that decays)
* intraday volume seasonality (busy around the EU and US sessions)
* heavy-tailed trade sizes with occasional whales
* buy/sell flow that leans with recent price direction
* millisecond timestamps, so same-timestamp ties occur and the trade_id tie-breaker is exercised
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from mtm.schema import TRADE_COLUMNS

SECONDS = 86_400
DEFAULT_START_PRICES = {"BTCUSDT": 95_000.0, "ETHUSDT": 3_300.0}
# (beta to the market factor, idiosyncratic volatility share, share of total trades)
SYMBOL_PARAMS = {"BTCUSDT": (1.0, 0.35, 1.0), "ETHUSDT": (1.15, 0.55, 0.6)}
BASE_SIGMA = 8e-5  # per-second return volatility in a calm regime (~2.3% daily)


def _volume_profile() -> np.ndarray:
    hours = np.arange(SECONDS) / 3600.0
    return (
        0.5
        + np.exp(-(((hours - 14.0) / 3.0) ** 2))
        + 0.6 * np.exp(-(((hours - 8.0) / 2.5) ** 2))
    )


def _sigma_path(rng: np.random.Generator, shock: bool) -> np.ndarray:
    minutes = SECONDS // 60
    x = np.zeros(minutes)
    noise = rng.normal(0.0, 0.06, minutes)
    for i in range(1, minutes):
        x[i] = 0.995 * x[i - 1] + noise[i]
    sigma = BASE_SIGMA * np.exp(np.repeat(x, 60))
    if shock:
        t0 = int(rng.integers(3600, SECONDS - 3600))
        decay = np.exp(-np.maximum(np.arange(SECONDS) - t0, 0) / 600.0)
        sigma = sigma * (1.0 + 3.0 * decay * (np.arange(SECONDS) >= t0))
    return sigma


def generate_day(
    symbols: list[str],
    day: date,
    n_trades: int,
    seed: int = 7,
    start_prices: dict[str, float] | None = None,
    shock: bool = True,
) -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
    """Generate one UTC day of trades per symbol. Returns (frames, end_prices)."""
    rng = np.random.default_rng([seed, day.toordinal()])
    start_prices = {**DEFAULT_START_PRICES, **(start_prices or {})}
    sigma = _sigma_path(rng, shock)
    factor = rng.normal(0.0, 1.0, SECONDS) * sigma
    profile = _volume_profile()
    intensity = profile * (sigma / BASE_SIGMA) ** 0.5
    weights = intensity / intensity.sum()
    day_start_us = int(pd.Timestamp(day).value // 1000)  # midnight UTC, machine independent

    frames: dict[str, pd.DataFrame] = {}
    end_prices: dict[str, float] = {}
    for symbol in symbols:
        beta, idio_share, trade_share = SYMBOL_PARAMS.get(symbol, (1.0, 0.5, 1.0))
        returns = beta * factor + idio_share * sigma * rng.normal(0.0, 1.0, SECONDS)
        path = start_prices[symbol] * np.exp(np.cumsum(returns))
        end_prices[symbol] = float(path[-1])

        n = max(1, int(n_trades * trade_share))
        secs = rng.choice(SECONDS, size=n, p=weights)
        millis = rng.integers(0, 1000, size=n)
        ts_us = day_start_us + secs.astype("int64") * 1_000_000 + millis.astype("int64") * 1000
        order = np.argsort(ts_us, kind="stable")
        ts_us, secs = ts_us[order], secs[order]

        micro = rng.normal(0.0, 1e-5, n)
        price = np.round(path[secs] * (1.0 + micro), 2)

        qty = np.round(np.exp(rng.normal(-3.0, 1.3, n)), 5)
        whales = rng.random(n) < 0.001
        qty = np.where(whales, qty * 50.0, qty)
        qty = np.maximum(qty, 1e-5)

        cum = np.cumsum(returns)
        lagged = np.concatenate([np.zeros(30), cum[:-30]])
        trend = (cum - lagged)[secs] / (3.0 * sigma[secs] * np.sqrt(30))
        p_seller_aggressive = 0.5 - 0.25 * np.tanh(trend)
        is_buyer_maker = rng.random(n) < p_seller_aggressive

        trade_id = (day.toordinal() - 739_000) * 10_000_000 + np.arange(n, dtype="int64")
        frames[symbol] = pd.DataFrame(
            {
                "ts": pd.to_datetime(ts_us, unit="us").astype("datetime64[us]"),
                "symbol": pd.Series([symbol] * n, dtype="string"),
                "trade_id": trade_id,
                "price": price.astype("float64"),
                "qty": qty.astype("float64"),
                "is_buyer_maker": is_buyer_maker,
            }
        )[TRADE_COLUMNS]
    return frames, end_prices


def generate_range(
    symbols: list[str],
    start: date,
    n_days: int,
    n_trades: int,
    seed: int = 7,
) -> dict[tuple[str, date], pd.DataFrame]:
    """Generate consecutive days, carrying each day's closing price into the next open."""
    out: dict[tuple[str, date], pd.DataFrame] = {}
    prices: dict[str, float] | None = None
    for i in range(n_days):
        day = start + timedelta(days=i)
        frames, prices = generate_day(symbols, day, n_trades, seed=seed, start_prices=prices)
        for symbol, df in frames.items():
            out[(symbol, day)] = df
    return out

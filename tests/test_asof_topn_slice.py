"""Hand-checkable cases for Q4 (as-of join), Q5 (top-N) and Q6 (narrow slice)."""

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mtm import store
from mtm.engines import (
    DuckDBEngine,
    DuckDBTableEngine,
    PandasEngine,
    ReferenceEngine,
    assert_results_equal,
)
from mtm.schema import TRADE_COLUMNS

ALL = [PandasEngine, DuckDBEngine, DuckDBTableEngine, ReferenceEngine]
DAY = date(2025, 1, 2)
T0 = pd.Timestamp("2025-01-02 12:00:00")


def _frame(symbol: str, rows: list[tuple]) -> pd.DataFrame:
    """rows: (seconds_after_T0, trade_id, price, qty)"""
    df = pd.DataFrame(rows, columns=["sec", "trade_id", "price", "qty"])
    df["ts"] = (T0 + pd.to_timedelta(df["sec"], unit="s")).astype("datetime64[us]")
    df["symbol"] = symbol
    df["is_buyer_maker"] = False
    return df[TRADE_COLUMNS]


def _dataset(root: Path, btc: list[tuple], eth: list[tuple]) -> store.DatasetRef:
    store.write_day(_frame("BTCUSDT", btc), root, "BTCUSDT", DAY)
    store.write_day(_frame("ETHUSDT", eth), root, "ETHUSDT", DAY)
    return store.DatasetRef(root)


def _each(ref, query_id, params):
    out = {}
    for cls in ALL:
        e = cls()
        e.load(ref)
        out[cls.name] = e.run(query_id, params)
    return out


Q4 = {"symbol": "ETHUSDT", "other": "BTCUSDT"}


def test_asof_never_uses_a_future_price(tmp_path):
    # The only BTC trade happens 5 seconds AFTER the ETH trade: a look-ahead join would match it.
    ref = _dataset(tmp_path, btc=[(5, 1, 50_000.0, 1.0)], eth=[(0, 1, 2_500.0, 1.0)])
    for name, res in _each(ref, "Q4", Q4).items():
        assert len(res) == 0, f"{name} matched a BTC price from the future"


def test_asof_uses_exact_timestamp_match_and_latest_prior(tmp_path):
    ref = _dataset(
        tmp_path,
        btc=[(0, 1, 100.0, 1.0), (10, 2, 200.0, 1.0), (20, 3, 400.0, 1.0)],
        eth=[(5, 1, 10.0, 1.0), (10, 2, 10.0, 1.0), (15, 3, 10.0, 1.0)],
    )
    # ETH@5 -> BTC@0 (100); ETH@10 -> BTC@10 exactly (200); ETH@15 -> BTC@10 (200)
    expected = (10 / 100 + 10 / 200 + 10 / 200) / 3
    for name, res in _each(ref, "Q4", Q4).items():
        assert len(res) == 1 and res["trades"].iloc[0] == 3, name
        assert res["ratio"].iloc[0] == pytest.approx(expected), name


def test_asof_tie_on_timestamp_takes_highest_trade_id(tmp_path):
    ref = _dataset(
        tmp_path,
        btc=[(0, 7, 300.0, 1.0), (0, 3, 100.0, 1.0), (0, 5, 200.0, 1.0)],
        eth=[(1, 1, 30.0, 1.0)],
    )
    for name, res in _each(ref, "Q4", Q4).items():
        assert res["ratio"].iloc[0] == pytest.approx(30.0 / 300.0), name


def test_asof_drops_eth_trades_before_first_btc_trade(tmp_path):
    ref = _dataset(
        tmp_path,
        btc=[(10, 1, 100.0, 1.0)],
        eth=[(0, 1, 10.0, 1.0), (9, 2, 10.0, 1.0), (12, 3, 20.0, 1.0)],
    )
    for name, res in _each(ref, "Q4", Q4).items():
        assert res["trades"].iloc[0] == 1, name
        assert res["ratio"].iloc[0] == pytest.approx(0.2), name


def test_top_n_breaks_qty_ties_by_time_then_trade_id(tmp_path):
    ref = _dataset(
        tmp_path,
        btc=[
            (0, 4, 100.0, 5.0),
            (1, 2, 101.0, 5.0),
            (1, 1, 102.0, 5.0),  # same ts and qty as trade 2: lower trade_id wins
            (2, 3, 103.0, 9.0),
            (3, 5, 104.0, 1.0),
        ],
        eth=[(0, 1, 10.0, 1.0)],
    )
    params = {"symbol": "BTCUSDT", "n": 3}
    for name, res in _each(ref, "Q5", params).items():
        assert res["trade_id"].tolist() == [3, 4, 1], name


def test_top_n_with_fewer_trades_than_n(tmp_path):
    ref = _dataset(tmp_path, btc=[(0, 1, 100.0, 1.0), (1, 2, 100.0, 2.0)], eth=[(0, 1, 1.0, 1.0)])
    for name, res in _each(ref, "Q5", {"symbol": "BTCUSDT", "n": 10}).items():
        assert res["qty"].tolist() == [2.0, 1.0], name


def test_slice_is_half_open_and_handles_empty(tmp_path):
    ref = _dataset(
        tmp_path,
        btc=[(0, 1, 100.0, 1.0), (10, 2, 100.0, 2.0), (20, 3, 100.0, 4.0)],
        eth=[(0, 1, 1.0, 1.0)],
    )
    start, end = str(T0 + pd.Timedelta(seconds=10)), str(T0 + pd.Timedelta(seconds=20))
    for name, res in _each(ref, "Q6", {"symbol": "BTCUSDT", "start": start, "end": end}).items():
        assert (res["trades"].iloc[0], res["volume"].iloc[0]) == (1, 2.0), name  # 10 in, 20 out
    far = {"symbol": "BTCUSDT", "start": "2025-01-02 01:00:00", "end": "2025-01-02 01:00:05"}
    for name, res in _each(ref, "Q6", far).items():
        assert (res["trades"].iloc[0], res["volume"].iloc[0]) == (0, 0.0), name


def test_q6_requires_bounds_and_rejects_reversed_range(tmp_path):
    ref = _dataset(tmp_path, btc=[(0, 1, 100.0, 1.0)], eth=[(0, 1, 1.0, 1.0)])
    e = PandasEngine()
    e.load(ref)
    with pytest.raises(ValueError):
        e.run("Q6", {"symbol": "BTCUSDT"})
    with pytest.raises(ValueError):
        e.run("Q6", {"symbol": "BTCUSDT", "start": "2025-01-02 02:00", "end": "2025-01-02 01:00"})
    with pytest.raises(ValueError):
        e.run("Q4", {"symbol": "BTCUSDT", "other": "BTCUSDT"})


def test_duckdb_explain_returns_plan_text(tmp_path):
    ref = _dataset(tmp_path, btc=[(0, 1, 100.0, 1.0)], eth=[(1, 1, 10.0, 1.0)])
    e = DuckDBEngine()
    e.load(ref)
    plan = e.explain("Q4", Q4)
    # The operator DuckDB picks depends on table size, so only check the plan is a real one.
    assert "Join" in plan and "Table Scan" in plan


_trade = st.tuples(st.integers(0, 3600), st.floats(10.0, 500.0, allow_nan=False), st.floats(0.01, 20.0, allow_nan=False))


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(_trade, min_size=1, max_size=40),
    st.lists(_trade, min_size=1, max_size=40),
    st.integers(1, 8),
    st.integers(0, 3600),
    st.integers(1, 1200),
)
def test_random_datasets_agree_for_q4_q5_q6(btc_raw, eth_raw, n, slice_start, slice_len):
    btc = [(s, i, p, q) for i, (s, p, q) in enumerate(btc_raw)]
    eth = [(s, i, p, q) for i, (s, p, q) in enumerate(eth_raw)]
    start = T0 + pd.Timedelta(seconds=slice_start)
    end = start + pd.Timedelta(seconds=slice_len)
    cases = [
        ("Q4", Q4),
        ("Q5", {"symbol": "BTCUSDT", "n": n, "start": start, "end": end}),
        ("Q6", {"symbol": "ETHUSDT", "start": start, "end": end}),
    ]
    with tempfile.TemporaryDirectory() as d:
        ref = _dataset(Path(d), btc, eth)
        for query_id, params in cases:
            res = _each(ref, query_id, params)
            assert_results_equal(res["pandas"], res["reference"])
            assert_results_equal(res["duckdb"], res["reference"])
            assert_results_equal(res["duckdb-mem"], res["reference"])

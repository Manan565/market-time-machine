"""Every engine must give the same answer. pandas and DuckDB are checked against a slow,
obviously correct pure-Python reference."""

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
from mtm.ingest.synthetic import generate_range
from mtm.queries import QUERIES, standard_params
from mtm.schema import TRADE_COLUMNS

ALL_ENGINES = [PandasEngine, DuckDBEngine, DuckDBTableEngine, ReferenceEngine]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("parity")
    frames = generate_range(["BTCUSDT", "ETHUSDT"], date(2025, 1, 2), 2, 30_000)
    for (symbol, day), df in frames.items():
        store.write_day(df, root, symbol, day)
    return store.DatasetRef(root)


def _loaded(engine_cls, dataset):
    e = engine_cls()
    e.load(dataset)
    return e


DAY = date(2025, 1, 2)
CASES = [(qid, standard_params(qid, DAY)) for qid in sorted(QUERIES)] + [
    ("Q1", {"symbol": "ETHUSDT"}),
    ("Q2", {"symbol": "ETHUSDT"}),
    ("Q3", {"symbol": "ETHUSDT"}),
    ("Q4", {"symbol": "BTCUSDT", "other": "ETHUSDT"}),
    ("Q5", {**standard_params("Q5", DAY), "symbol": "ETHUSDT", "n": 25}),
    ("Q5", {"symbol": "BTCUSDT", "n": 5}),  # no time bounds at all
    ("Q6", {**standard_params("Q6", DAY), "symbol": "ETHUSDT"}),
    ("Q6", {"symbol": "BTCUSDT", "start": "2025-01-02 03:00:00", "end": "2025-01-03 03:00:00"}),
]


@pytest.mark.parametrize(("query_id", "params"), CASES, ids=lambda v: v if isinstance(v, str) else "")
def test_engines_agree_with_reference(dataset, query_id, params):
    results = {cls.name: _loaded(cls, dataset).run(query_id, params) for cls in ALL_ENGINES}
    assert len(results["reference"]) > 0
    assert_results_equal(results["pandas"], results["reference"])
    assert_results_equal(results["duckdb"], results["reference"])
    assert_results_equal(results["duckdb-mem"], results["reference"])


@pytest.mark.parametrize("minutes", [1, 3, 5, 7, 15, 60])
def test_q1_bucket_sizes_agree(dataset, minutes):
    params = {"symbol": "BTCUSDT", "bucket_minutes": minutes}
    ref = _loaded(ReferenceEngine, dataset).run("Q1", params)
    assert_results_equal(_loaded(PandasEngine, dataset).run("Q1", params), ref)
    assert_results_equal(_loaded(DuckDBEngine, dataset).run("Q1", params), ref)


@pytest.mark.parametrize("window", [5, 30, 60, 120])
def test_q3_window_sizes_agree(dataset, window):
    params = {"symbol": "BTCUSDT", "window": window}
    ref = _loaded(ReferenceEngine, dataset).run("Q3", params)
    assert_results_equal(_loaded(PandasEngine, dataset).run("Q3", params), ref)
    assert_results_equal(_loaded(DuckDBEngine, dataset).run("Q3", params), ref)


def test_vwap_lies_between_low_and_high(dataset):
    vwap = _loaded(DuckDBEngine, dataset).run("Q1", {"symbol": "BTCUSDT", "bucket_minutes": 1})
    bars = _loaded(DuckDBEngine, dataset).run("Q2", {"symbol": "BTCUSDT"})
    merged = vwap.merge(bars, on="bucket")
    assert (merged["vwap"] >= merged["low"] - 1e-6).all()
    assert (merged["vwap"] <= merged["high"] + 1e-6).all()


def test_q3_empty_when_fewer_bars_than_window(dataset):
    params = {"symbol": "BTCUSDT", "window": 10_000}
    for cls in ALL_ENGINES:
        assert len(_loaded(cls, dataset).run("Q3", params)) == 0


def test_invalid_params_rejected(dataset):
    e = _loaded(PandasEngine, dataset)
    with pytest.raises(ValueError):
        e.run("Q1", {"symbol": "btc; DROP TABLE x"})
    with pytest.raises(ValueError):
        e.run("Q1", {"symbol": "BTCUSDT", "bucket_minutes": 0})
    with pytest.raises(KeyError):
        e.run("Q99", {"symbol": "BTCUSDT"})


def _write(root: Path, rows: list[tuple]) -> store.DatasetRef:
    df = pd.DataFrame(rows, columns=["ts", "trade_id", "price", "qty"])
    df["ts"] = pd.to_datetime(df["ts"], format="ISO8601").astype("datetime64[us]")
    df["symbol"] = "BTCUSDT"
    df["is_buyer_maker"] = False
    store.write_day(df[TRADE_COLUMNS], root, "BTCUSDT", date(2025, 1, 2))
    return store.DatasetRef(root)


def test_ties_resolved_by_trade_id_not_arrival_order(tmp_path):
    # Three trades share one timestamp and arrive out of trade_id order.
    ref = _write(
        tmp_path,
        [
            ("2025-01-02 00:00:00.500", 30, 103.0, 1.0),
            ("2025-01-02 00:00:00.500", 10, 101.0, 1.0),
            ("2025-01-02 00:00:00.500", 20, 102.0, 1.0),
            ("2025-01-02 00:00:30", 40, 99.0, 2.0),
        ],
    )
    for cls in ALL_ENGINES:
        e = cls()
        e.load(ref)
        bar = e.run("Q2", {"symbol": "BTCUSDT"}).iloc[0]
        assert (bar["open"], bar["close"]) == (101.0, 99.0), cls.name
        assert (bar["high"], bar["low"]) == (103.0, 99.0), cls.name
        assert bar["trades"] == 4


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            st.integers(0, 4 * 3600 * 1000),  # milliseconds into the day, many collisions
            st.floats(10.0, 1000.0, allow_nan=False),
            st.floats(0.001, 50.0, allow_nan=False),
        ),
        min_size=1,
        max_size=60,
    ),
    st.sampled_from([1, 2, 5, 13]),
)
def test_random_small_datasets_agree(raw, minutes):
    base = pd.Timestamp("2025-01-02")
    rows = [(base + pd.Timedelta(milliseconds=ms), i, p, q) for i, (ms, p, q) in enumerate(raw)]
    with tempfile.TemporaryDirectory() as d:
        ref = _write(Path(d), rows)
        out = {}
        for cls in ALL_ENGINES:
            e = cls()
            e.load(ref)
            out[cls.name] = (
                e.run("Q1", {"symbol": "BTCUSDT", "bucket_minutes": minutes}),
                e.run("Q2", {"symbol": "BTCUSDT"}),
                e.run("Q3", {"symbol": "BTCUSDT", "window": 3}),
            )
    for name in ("pandas", "duckdb", "duckdb-mem"):
        for got, want in zip(out[name], out["reference"], strict=True):
            assert_results_equal(got, want)

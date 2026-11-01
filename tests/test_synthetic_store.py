from datetime import date

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from mtm import store
from mtm.ingest import quality
from mtm.ingest.synthetic import generate_day, generate_range
from mtm.schema import TRADE_COLUMNS

DAY = date(2025, 1, 2)


def test_generator_is_deterministic():
    a, _ = generate_day(["BTCUSDT"], DAY, 5_000, seed=1)
    b, _ = generate_day(["BTCUSDT"], DAY, 5_000, seed=1)
    pd.testing.assert_frame_equal(a["BTCUSDT"], b["BTCUSDT"])


def test_seed_changes_output():
    a, _ = generate_day(["BTCUSDT"], DAY, 5_000, seed=1)
    b, _ = generate_day(["BTCUSDT"], DAY, 5_000, seed=2)
    assert not a["BTCUSDT"]["price"].equals(b["BTCUSDT"]["price"])


def test_generated_data_is_clean_and_in_one_utc_day():
    frames, _ = generate_day(["BTCUSDT", "ETHUSDT"], DAY, 20_000)
    for df in frames.values():
        assert list(df.columns) == TRADE_COLUMNS
        report = quality.check(df)
        assert report.n_nonpositive_price == 0 and report.n_nonpositive_qty == 0
        assert report.n_backwards_ts == 0
        assert report.n_duplicate_trade_ids == 0
        assert df["ts"].min() >= pd.Timestamp(DAY)
        assert df["ts"].max() < pd.Timestamp(DAY) + pd.Timedelta(days=1)


def test_generated_data_contains_timestamp_ties():
    # Ties are deliberate: they force engines to agree on first/last via trade_id.
    frames, _ = generate_day(["BTCUSDT"], DAY, 100_000)
    assert frames["BTCUSDT"]["ts"].duplicated().sum() > 0


def test_btc_and_eth_are_correlated():
    frames, _ = generate_day(["BTCUSDT", "ETHUSDT"], DAY, 100_000)
    minute = {
        s: df.set_index("ts")["price"].resample("5min").last().ffill().pct_change().dropna()
        for s, df in frames.items()
    }
    corr = np.corrcoef(minute["BTCUSDT"], minute["ETHUSDT"])[0, 1]
    assert corr > 0.5


def test_range_carries_close_into_next_open():
    r = generate_range(["BTCUSDT"], DAY, 2, 20_000)
    day1, day2 = r[("BTCUSDT", DAY)], r[("BTCUSDT", date(2025, 1, 3))]
    gap = abs(day2["price"].iloc[0] / day1["price"].iloc[-1] - 1)
    assert gap < 0.01


def test_store_roundtrip_sorted_and_compressed(tmp_path):
    frames, _ = generate_day(["BTCUSDT"], DAY, 10_000)
    df = frames["BTCUSDT"].sample(frac=1.0, random_state=0)  # shuffle on purpose
    path = store.write_day(df, tmp_path, "BTCUSDT", DAY)
    assert path == tmp_path / "symbol=BTCUSDT" / "date=2025-01-02" / "part-0.parquet"
    back = pd.read_parquet(path)
    assert back["ts"].is_monotonic_increasing
    assert len(back) == 10_000
    assert pq.ParquetFile(path).metadata.row_group(0).column(0).compression == "ZSTD"


def test_dataset_ref_lists_files_and_days(tmp_path):
    for d in (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 4)):
        frames, _ = generate_day(["BTCUSDT"], d, 1_000)
        store.write_day(frames["BTCUSDT"], tmp_path, "BTCUSDT", d)
    ref = store.DatasetRef(tmp_path)
    assert ref.symbols() == ["BTCUSDT"]
    assert len(ref.files("BTCUSDT")) == 3
    assert ref.days("BTCUSDT")[0] == date(2025, 1, 2)
    assert len(ref.files("BTCUSDT", start=date(2025, 1, 3))) == 2
    assert ref.row_count("BTCUSDT") == 3_000


def test_day_summary_and_merge(tmp_path):
    frames, _ = generate_day(["BTCUSDT"], DAY, 10_000)
    row = store.day_summary(frames["BTCUSDT"], "BTCUSDT", DAY)
    assert row["low"] <= row["open"] <= row["high"]
    assert row["low"] <= row["close"] <= row["high"]
    assert abs(row["buy_volume"] + row["sell_volume"] - row["volume"]) < 1e-6
    assert row["n_trades"] == 10_000
    store.update_summary(tmp_path, [row])
    row2 = {**row, "n_trades": 1}
    merged = store.update_summary(tmp_path, [row2])
    assert len(merged) == 1 and merged["n_trades"].iloc[0] == 1

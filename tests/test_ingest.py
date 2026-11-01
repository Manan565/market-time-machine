import io
import zipfile

import pandas as pd

from mtm.ingest import binance, quality
from mtm.schema import TRADE_COLUMNS

MS_CSV = """100,42000.10,0.5,21000.05,1735862400000,True,True
101,42000.20,0.1,4200.02,1735862400500,False,True
102,42001.00,2.0,84002.00,1735862401000,True,True
"""

US_CSV = """100,42000.10,0.5,21000.05,1735862400000123,True,True
101,42000.20,0.1,4200.02,1735862400500456,False,True
"""


def test_parse_milliseconds():
    df = binance.parse_csv(io.StringIO(MS_CSV), "BTCUSDT")
    assert list(df.columns) == TRADE_COLUMNS
    assert df["ts"].iloc[0] == pd.Timestamp("2025-01-03 00:00:00")
    assert df["ts"].iloc[1] == pd.Timestamp("2025-01-03 00:00:00.500")
    assert df["is_buyer_maker"].tolist() == [True, False, True]
    assert (df["symbol"] == "BTCUSDT").all()


def test_parse_microseconds_detected():
    df = binance.parse_csv(io.StringIO(US_CSV), "BTCUSDT")
    assert df["ts"].iloc[0] == pd.Timestamp("2025-01-03 00:00:00.000123")
    assert df["ts"].iloc[1] == pd.Timestamp("2025-01-03 00:00:00.500456")


def test_header_row_is_dropped():
    header = "id,price,qty,quote_qty,time,is_buyer_maker,is_best_match\n"
    df = binance.parse_csv(io.StringIO(header + MS_CSV), "BTCUSDT")
    assert len(df) == 3
    assert df["trade_id"].tolist() == [100, 101, 102]


def test_parse_zip_roundtrip(tmp_path):
    zip_path = tmp_path / "BTCUSDT-trades-2025-01-02.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("BTCUSDT-trades-2025-01-02.csv", MS_CSV)
    df = binance.parse_zip(zip_path, "BTCUSDT")
    assert len(df) == 3


def test_checksum_helper(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    assert binance.sha256_of(p) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def _frame(rows):
    df = pd.DataFrame(rows, columns=["ts", "trade_id", "price", "qty"])
    df["ts"] = pd.to_datetime(df["ts"]).astype("datetime64[us]")
    df["symbol"] = "BTCUSDT"
    df["is_buyer_maker"] = False
    return df[TRADE_COLUMNS]


def test_quality_counts_every_problem():
    df = _frame(
        [
            ("2025-01-02 00:00:00", 1, 100.0, 1.0),
            ("2025-01-02 00:00:01", 2, 101.0, 1.0),
            ("2025-01-02 00:00:01", 2, 101.0, 1.0),  # exact duplicate
            ("2025-01-02 00:00:00", 3, 100.5, 1.0),  # timestamp goes backwards
            ("2025-01-02 00:10:00", 4, -5.0, 1.0),  # negative price, 10 minute gap
            ("2025-01-02 00:10:01", 5, 100.0, 0.0),  # zero quantity
        ]
    )
    r = quality.check(df)
    assert r.n_rows == 6
    assert r.n_duplicate_rows == 1
    assert r.n_duplicate_trade_ids == 1
    assert r.n_nonpositive_price == 1
    assert r.n_nonpositive_qty == 1
    assert r.n_backwards_ts == 1
    assert r.n_gaps_over_threshold == 1
    assert not r.is_clean


def test_clean_drops_bad_rows_and_sorts():
    df = _frame(
        [
            ("2025-01-02 00:00:01", 2, 101.0, 1.0),
            ("2025-01-02 00:00:00", 1, 100.0, 1.0),
            ("2025-01-02 00:00:01", 2, 101.0, 1.0),
            ("2025-01-02 00:00:02", 3, -1.0, 1.0),
        ]
    )
    cleaned, report = quality.clean(df)
    assert cleaned["trade_id"].tolist() == [1, 2]
    assert report.n_duplicate_rows == 1
    assert report.n_nonpositive_price == 1


def test_clean_data_reports_clean():
    df = _frame([("2025-01-02 00:00:00", 1, 100.0, 1.0), ("2025-01-02 00:00:01", 2, 101.0, 2.0)])
    assert quality.check(df).is_clean

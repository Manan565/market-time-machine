"""Replay core: frames must add up to the day's totals, exactly."""

from datetime import date

import numpy as np
import pytest

from mtm import store
from mtm.api.replay import DAY_SECONDS, DayCache, DayData, ReplaySession
from mtm.ingest.synthetic import generate_day

DAY = date(2025, 1, 2)


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    root = tmp_path_factory.mktemp("replay")
    frames, _ = generate_day(["BTCUSDT"], DAY, 40_000)
    store.write_day(frames["BTCUSDT"], root, "BTCUSDT", DAY)
    return root


@pytest.fixture(scope="module")
def data(root):
    return DayData.load(root, "BTCUSDT", DAY)


def test_frames_partition_the_day_exactly(data):
    session = ReplaySession(data, speed=20_000)
    frames = []
    while (f := session.step(0.05)) is not None:
        frames.append(f)
    assert session.finished
    assert sum(f["n"] for f in frames) == len(data)
    assert sum(f["volume"] for f in frames) == pytest.approx(data.qty.sum(), rel=1e-9)
    last = frames[-1]
    assert last["cum_trades"] == len(data)
    assert last["cum_vwap"] == pytest.approx((data.price * data.qty).sum() / data.qty.sum())


def test_frame_buy_and_sell_volume_add_up(data):
    f = data.frame(0, DAY_SECONDS)
    assert f["buy_volume"] + f["sell_volume"] == pytest.approx(f["volume"])
    assert f["sell_volume"] == pytest.approx(data.qty[data.sell].sum())


def test_frame_window_is_half_open(data):
    # A window ending exactly at a trade's timestamp must not include that trade.
    t = (data.ts_us[100] - data.t0_us) / 1_000_000
    assert data.frame(0, t)["cum_trades"] == data.index(t) <= 100
    assert data.frame(0, t + 0.001)["cum_trades"] >= data.index(t) + 1


def test_empty_window_reports_running_totals_and_last_price(data):
    t = 12 * 3600.0
    f = data.frame(t, t)
    assert f["n"] == 0 and f["volume"] == 0.0 and f["max_qty"] == 0.0
    assert f["cum_trades"] == data.index(t)
    assert f["last"] == data.price[data.index(t) - 1]


def test_frame_before_first_trade_has_no_price(data):
    f = data.frame(0, 0)
    assert f["last"] is None and f["cum_vwap"] is None and f["cum_trades"] == 0


def test_high_low_and_max_qty_inside_window(data):
    f = data.frame(10 * 3600, 11 * 3600)
    lo, hi = data.index(10 * 3600), data.index(11 * 3600)
    assert f["high"] == data.price[lo:hi].max() and f["low"] == data.price[lo:hi].min()
    assert f["max_qty"] == data.qty[lo:hi].max()
    assert f["open"] == data.price[lo] and f["last"] == data.price[hi - 1]


def test_bars_are_full_day_and_consistent(data):
    b = data.bars(1)
    assert len(b["t"]) == 1440
    assert not any(np.isnan(b["close"])) and not any(np.isnan(b["cum_vwap"]))
    assert sum(b["volume"]) == pytest.approx(data.qty.sum())
    assert b["cum_vwap"][-1] == pytest.approx((data.price * data.qty).sum() / data.qty.sum())
    assert all(h >= lo for h, lo in zip(b["high"], b["low"], strict=True))
    assert len(data.bars(15)["t"]) == 96


def test_bar_timestamps_are_utc_milliseconds(data):
    b = data.bars(5)
    assert b["t"][0] == int(np.datetime64("2025-01-02T00:00:00", "ms").astype("int64"))
    assert b["t"][1] - b["t"][0] == 5 * 60 * 1000


def test_session_controls(data):
    s = ReplaySession(data)
    s.set_speed(10**9)
    assert s.speed == 20_000
    s.set_speed(-5)
    assert s.speed == 1
    snap = s.seek(DAY_SECONDS * 2)  # clamped to the end of the day
    assert s.pos_s == DAY_SECONDS and snap["cum_trades"] == len(data)
    assert s.step(0.05) is None and not s.playing
    s.seek(-10)
    assert s.pos_s == 0


def test_seek_then_step_continues_from_that_point(data):
    s = ReplaySession(data, speed=600)
    s.seek(3600)
    f = s.step(0.05)  # 600x for 50 ms = 30 simulated seconds
    assert f["offset_s"] == pytest.approx(3630)


def test_cache_returns_same_object_and_evicts(root):
    cache = DayCache(root, max_days=1)
    a = cache.get("BTCUSDT", DAY)
    assert cache.get("BTCUSDT", DAY) is a
    with pytest.raises(FileNotFoundError):
        cache.get("BTCUSDT", date(2030, 1, 1))

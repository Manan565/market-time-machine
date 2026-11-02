"""HTTP and websocket behaviour of the service, on a small synthetic dataset."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mtm import store
from mtm.api.app import create_app
from mtm.ingest.synthetic import generate_range
from mtm.queries import QUERIES

DAY = "2025-01-02"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("api") / "parquet"
    summaries = []
    for (symbol, day), df in generate_range(["BTCUSDT", "ETHUSDT"], date(2025, 1, 2), 2, 20_000).items():
        store.write_day(df, root, symbol, day)
        summaries.append(store.day_summary(df, symbol, day))
    store.update_summary(root, summaries)
    bench = root.parent / "bench.json"
    bench.write_text(json.dumps({"meta": {"warm_runs": 7}, "runs": []}))
    with TestClient(create_app(root, bench)) as c:
        yield c


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert body["engines"] == ["pandas", "duckdb", "duckdb-mem"]


def test_days_listing_is_ranked_by_range(client):
    days = client.get("/api/days").json()["days"]
    assert len(days) == 4
    btc = sorted((d for d in days if d["symbol"] == "BTCUSDT"), key=lambda d: d["range_rank"])
    assert [d["range_rank"] for d in btc] == [1, 2]
    assert btc[0]["range_pct"] >= btc[1]["range_pct"]
    assert days[0]["date"] in {"2025-01-02", "2025-01-03"}


def test_bars_endpoint(client):
    body = client.get(f"/api/days/BTCUSDT/{DAY}/bars").json()
    assert len(body["t"]) == 1440 and body["n_trades"] == 20_000
    assert len(client.get(f"/api/days/BTCUSDT/{DAY}/bars?minutes=15").json()["t"]) == 96
    assert client.get(f"/api/days/BTCUSDT/{DAY}/bars?minutes=7").status_code == 400
    assert client.get("/api/days/BTCUSDT/2030-01-01/bars").status_code == 404
    assert client.get("/api/days/DOGEUSDT/2025-01-02/bars").status_code == 404
    assert client.get("/api/days/BTCUSDT/not-a-date/bars").status_code == 400


def test_query_catalog(client):
    body = client.get("/api/queries").json()
    assert [q["id"] for q in body["queries"]] == list(QUERIES)
    q5 = next(q for q in body["queries"] if q["id"] == "Q5")
    assert q5["default_params"]["start"].startswith("2025-01-02")
    assert [e["id"] for e in body["engines"]] == ["pandas", "duckdb", "duckdb-mem"]


@pytest.mark.parametrize("query_id", list(QUERIES))
def test_race_runs_every_query_and_engines_agree(client, query_id):
    r = client.post("/api/query/run", json={"query": query_id, "repeats": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agree"] is True and body["disagreements"] == []
    assert len(body["results"]) == 3
    for res in body["results"]:
        assert res["median_ms"] > 0 and len(res["runs_ms"]) == 2 and res["rows"] >= 1
        assert "error" not in res and len(res["preview"]) <= 5
    assert body["winner"] in {"pandas", "duckdb", "duckdb-mem"}
    # two days per symbol; Q4 is anchored on ETHUSDT, which has 60% as many trades as BTCUSDT
    assert body["dataset_rows"] == (12_000 if query_id == "Q4" else 20_000) * 2


def test_race_exposes_sql_only_for_sql_engines(client):
    body = client.post("/api/query/run", json={"query": "Q1", "repeats": 1}).json()
    by = {r["engine"]: r for r in body["results"]}
    assert "time_bucket" in by["duckdb"]["sql"] and "sql" not in by["pandas"]


def test_race_accepts_overrides_and_subsets(client):
    r = client.post(
        "/api/query/run",
        json={
            "query": "Q1",
            "engines": ["pandas", "duckdb-mem"],
            "params": {"symbol": "ETHUSDT", "bucket_minutes": 15},
            "repeats": 1,
        },
    )
    body = r.json()
    assert [x["engine"] for x in body["results"]] == ["pandas", "duckdb-mem"]
    assert body["params"]["symbol"] == "ETHUSDT" and body["results"][0]["rows"] == 2 * 96  # two days of 15-minute buckets


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"query": "Q99"}, 404),
        ({"query": "Q1", "engines": ["pandas", "oracle"]}, 400),
        ({"query": "Q1", "engines": []}, 400),
        ({"query": "Q1", "engines": ["pandas", "pandas"]}, 400),
        ({"query": "Q1", "repeats": 50}, 422),
        ({"query": "Q1", "params": {"symbol": "DOGEUSDT"}}, 400),
        ({"query": "Q1", "params": {"symbol": "btc; DROP TABLE x"}}, 400),
        ({"query": "Q1", "params": {"bucket_minutes": 10**9}}, 400),
        ({"query": "Q1", "params": {"bucket_minutes": "5"}}, 400),
        ({"query": "Q1", "params": {"path": "/etc/passwd"}}, 400),
        ({"query": "Q5", "params": {"n": 10_000}}, 400),
        ({"query": "Q6", "params": {"start": "2025-01-02 05:00", "end": "2025-01-02 04:00"}}, 400),
        ({"query": "Q6", "params": {"start": "garbage"}}, 400),
        ({"query": "Q1", "day": "nope"}, 400),
    ],
)
def test_race_rejects_bad_requests(client, payload, status):
    assert client.post("/api/query/run", json=payload).status_code == status


def test_bench_results_served_and_missing_is_404(client, tmp_path):
    assert client.get("/api/bench/results").json()["meta"]["warm_runs"] == 7
    with TestClient(create_app(client.app.state.mtm.dataset.root, tmp_path / "missing.json")) as c:
        assert c.get("/api/bench/results").status_code == 404


def test_websocket_hello_seek_play_pause(client):
    with client.websocket_connect(f"/api/replay/BTCUSDT/{DAY}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["n_trades"] == 20_000
        assert ws.receive_json()["type"] == "snapshot"

        ws.send_json({"type": "seek", "offset_s": 43_200})
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["cum_trades"] > 0 and snap["offset_s"] == 43_200

        ws.send_json({"type": "play", "speed": 1000})
        frame = ws.receive_json()
        assert frame["type"] == "frame" and frame["offset_s"] > 43_200
        assert {"last", "volume", "buy_volume", "sell_volume", "max_qty", "cum_vwap"} <= set(frame)

        ws.send_json({"type": "pause"})
        # frames already in flight may still arrive; after a pause the offset must stop moving
        ws.send_json({"type": "seek", "offset_s": 100})
        while (msg := ws.receive_json())["type"] == "frame":
            pass
        assert msg["type"] == "snapshot" and msg["offset_s"] == 100


def test_websocket_plays_to_the_end_then_reports_it(client):
    with client.websocket_connect(f"/api/replay/BTCUSDT/{DAY}") as ws:
        ws.receive_json(), ws.receive_json()
        ws.send_json({"type": "seek", "offset_s": 86_400 - 1})
        ws.receive_json()
        ws.send_json({"type": "play", "speed": 20_000})
        total = 0
        while (msg := ws.receive_json())["type"] == "frame":
            total += msg["n"]
        assert msg["type"] == "end" and total >= 0


def test_websocket_survives_garbage_and_unknown_messages(client):
    with client.websocket_connect(f"/api/replay/BTCUSDT/{DAY}") as ws:
        ws.receive_json(), ws.receive_json()
        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "teleport"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "seek", "offset_s": "soon"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "seek", "offset_s": 10})
        assert ws.receive_json()["type"] == "snapshot"  # still alive


def test_websocket_rejects_unknown_day(client):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/replay/BTCUSDT/2030-01-01"):
        pass
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/replay/BTCUSDT/garbage"):
        pass

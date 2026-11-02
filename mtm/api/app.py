"""FastAPI service: replay stream, query race, catalog and recorded benchmark results.

Routes (all under /api):
  GET  /health
  GET  /days                               every stored day with summary stats, ranked by range
  GET  /days/{symbol}/{date}/bars          minute bars for the chart background
  WS   /replay/{symbol}/{date}             play / pause / seek / speed; frames every ~50 ms
  GET  /queries                            the query catalog with default parameters
  POST /query/run                          run one query on several engines and compare
  GET  /bench/results                      the recorded benchmark file, as committed
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median

import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mtm.api.replay import DayCache, ReplaySession
from mtm.engines import ENGINES, assert_results_equal
from mtm.plot import ENGINE_LABEL
from mtm.queries import QUERIES, resolve, standard_params
from mtm.store import DatasetRef

TICK_SECONDS = 0.05
ALLOWED_PARAMS = {"symbol", "other", "bucket_minutes", "window", "n", "start", "end"}
INT_LIMITS = {"bucket_minutes": 1440, "window": 1440, "n": 1000}
SLOW_WARMUP_SECONDS = 1.5  # a query this slow is only timed once more, not repeated
LOCK_WAIT_SECONDS = 3.0


@dataclass
class State:
    dataset: DatasetRef
    engines: dict
    days: DayCache
    bench_path: Path | None
    first_day: date
    run_lock: threading.Lock


class RunRequest(BaseModel):
    query: str
    params: dict = Field(default_factory=dict)
    engines: list[str] = Field(default_factory=lambda: list(ENGINES))
    repeats: int = Field(3, ge=1, le=7)
    day: str | None = None


def _load_state(root: Path, bench_path: Path | None) -> State:
    dataset = DatasetRef(root)
    symbols = dataset.symbols()
    if not symbols:
        raise RuntimeError(f"no data under {root}; run `make ingest-synth` first")
    engines = {}
    for name, cls in ENGINES.items():
        engine = cls()
        engine.load(dataset, symbols)
        engines[name] = engine
    return State(
        dataset=dataset,
        engines=engines,
        days=DayCache(root),
        bench_path=bench_path,
        first_day=min(dataset.days(symbols[0])),
        run_lock=threading.Lock(),
    )


def create_app(data_root: Path, bench_results: Path | None = None, web_dist: Path | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.mtm = await run_in_threadpool(_load_state, data_root, bench_results)
        yield

    app = FastAPI(title="Market Time Machine", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def st() -> State:
        return app.state.mtm

    def parse_day(text: str) -> date:
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise HTTPException(400, f"bad date {text!r}; use YYYY-MM-DD") from exc

    def need_day(symbol: str, day: str):
        d = parse_day(day)
        if symbol not in st().dataset.symbols() or d not in st().dataset.days(symbol):
            raise HTTPException(404, f"no data for {symbol} on {day}")
        return st().days.get(symbol, d)

    @app.get("/api/health")
    def health():
        s = st()
        return {
            "ok": True,
            "symbols": s.dataset.symbols(),
            "engines": list(s.engines),
            "synthetic": True,
        }

    @app.get("/api/days")
    def days():
        summary = st().dataset.summary().copy()
        summary["range_rank"] = summary.groupby("symbol")["range_pct"].rank(
            ascending=False, method="first"
        ).astype(int)
        summary["date"] = summary["date"].dt.strftime("%Y-%m-%d")
        records = json.loads(summary.to_json(orient="records"))
        return {"symbols": st().dataset.symbols(), "days": records}

    @app.get("/api/days/{symbol}/{day}/bars")
    def bars(symbol: str, day: str, minutes: int = 1):
        if minutes not in (1, 5, 15):
            raise HTTPException(400, "minutes must be 1, 5 or 15")
        data = need_day(symbol, day)
        return {**data.summary(), "minutes": minutes, **data.bars(minutes)}

    @app.websocket("/api/replay/{symbol}/{day}")
    async def replay(ws: WebSocket, symbol: str, day: str):
        try:
            data = await run_in_threadpool(st().days.get, symbol, date.fromisoformat(day))
        except (ValueError, FileNotFoundError):
            await ws.close(code=4404)
            return
        await ws.accept()
        session = ReplaySession(data)
        await ws.send_json({"type": "hello", **data.summary()})
        await ws.send_json({"type": "snapshot", **session.seek(0)})

        inbox: asyncio.Queue = asyncio.Queue()

        async def reader() -> None:
            try:
                while True:
                    text = await ws.receive_text()
                    try:
                        await inbox.put(json.loads(text))
                    except json.JSONDecodeError:
                        await inbox.put({"type": "invalid"})
            except WebSocketDisconnect:
                await inbox.put(None)

        reader_task = asyncio.create_task(reader())
        last = time.monotonic()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        inbox.get(), TICK_SECONDS if session.playing else None
                    )
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    frame = session.step(min(now - last, 0.25))
                    last = now
                    if frame is not None:
                        await ws.send_json({"type": "frame", **frame})
                    if session.finished:
                        session.playing = False
                        await ws.send_json({"type": "end"})
                    continue
                if msg is None:
                    break
                try:
                    kind = msg.get("type")
                    if "speed" in msg:
                        session.set_speed(float(msg["speed"]))
                    if kind == "play":
                        if session.finished:
                            session.seek(0)
                        session.playing = True
                        last = time.monotonic()
                    elif kind == "pause":
                        session.playing = False
                    elif kind == "seek":
                        snap = session.seek(float(msg["offset_s"]))
                        await ws.send_json({"type": "snapshot", **snap})
                        last = time.monotonic()
                    elif kind not in ("speed",):
                        await ws.send_json({"type": "error", "detail": f"unknown message {kind!r}"})
                except (KeyError, TypeError, ValueError, AttributeError):
                    await ws.send_json({"type": "error", "detail": "malformed message"})
        except WebSocketDisconnect:
            pass
        finally:
            reader_task.cancel()

    @app.get("/api/queries")
    def queries():
        day = st().first_day
        return {
            "queries": [
                {
                    "id": q.id,
                    "title": q.title,
                    "description": q.description,
                    "columns": list(q.columns),
                    "default_params": standard_params(q.id, day),
                }
                for q in QUERIES.values()
            ],
            "engines": [{"id": e, "label": ENGINE_LABEL.get(e, e)} for e in st().engines],
        }

    def clean_params(query_id: str, raw: dict, day: date) -> dict:
        unknown = set(raw) - ALLOWED_PARAMS
        if unknown:
            raise HTTPException(400, f"unsupported parameters: {sorted(unknown)}")
        merged = {**standard_params(query_id, day), **raw}
        symbols = st().dataset.symbols()
        for key in ("symbol", "other"):
            if key in merged and merged[key] not in symbols:
                raise HTTPException(400, f"{key} must be one of {symbols}")
        for key, cap in INT_LIMITS.items():
            if key in merged and not (isinstance(merged[key], int) and 1 <= merged[key] <= cap):
                raise HTTPException(400, f"{key} must be an integer from 1 to {cap}")
        try:
            resolve(query_id, merged)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return merged

    def race(query_id: str, params: dict, names: list[str], repeats: int) -> dict:
        s = st()
        results, frames = [], {}
        for name in names:
            engine = s.engines[name]
            entry = {"engine": name, "label": ENGINE_LABEL.get(name, name)}
            try:
                t0 = time.perf_counter()
                engine.run(query_id, params)  # warm-up, not counted
                warm = time.perf_counter() - t0
                runs = []
                for _ in range(1 if warm > SLOW_WARMUP_SECONDS else repeats):
                    t0 = time.perf_counter()
                    frame = engine.run(query_id, params)
                    runs.append((time.perf_counter() - t0) * 1000)
                frames[name] = frame
                entry |= {
                    "median_ms": median(runs),
                    "runs_ms": runs,
                    "rows": len(frame),
                    "preview": json.loads(frame.head(5).to_json(orient="records", date_format="iso")),
                }
                if hasattr(engine, "sql"):
                    entry["sql"] = engine.sql(query_id, params)
            except Exception as exc:  # noqa: BLE001 - report per-engine failures, keep racing
                entry["error"] = f"{type(exc).__name__}: {exc}"
            results.append(entry)
        disagreements = []
        ok = [n for n in names if n in frames]
        for other in ok[1:]:
            try:
                assert_results_equal(frames[ok[0]], frames[other])
            except AssertionError as exc:
                disagreements.append({"engines": [ok[0], other], "detail": str(exc)})
        timed = [r for r in results if "median_ms" in r]
        return {
            "query": query_id,
            "params": json.loads(pd.Series(params, dtype=object).to_json()),
            "dataset_rows": s.dataset.row_count(params["symbol"]),
            "results": results,
            "agree": not disagreements and len(ok) > 1,
            "disagreements": disagreements,
            "winner": min(timed, key=lambda r: r["median_ms"])["engine"] if timed else None,
            "method": (
                f"Median of up to {repeats} warm runs, with one warm-up run discarded "
                "and one engine running at a time"
            ),
        }

    @app.post("/api/query/run")
    def run_query(req: RunRequest):
        s = st()
        if req.query not in QUERIES:
            raise HTTPException(404, f"unknown query {req.query!r}; known: {sorted(QUERIES)}")
        bad = [e for e in req.engines if e not in s.engines]
        if bad or not req.engines or len(set(req.engines)) != len(req.engines):
            raise HTTPException(400, f"engines must be unique and from {list(s.engines)}")
        day = parse_day(req.day) if req.day else s.first_day
        params = clean_params(req.query, req.params, day)
        if not s.run_lock.acquire(timeout=LOCK_WAIT_SECONDS):
            raise HTTPException(429, "another race is running; try again in a moment")
        try:
            return race(req.query, params, req.engines, req.repeats)
        finally:
            s.run_lock.release()

    @app.get("/api/bench/results")
    def get_bench_results():
        path = st().bench_path
        if path is None or not path.exists():
            raise HTTPException(404, "no recorded benchmark results; run `make bench`")
        return json.loads(path.read_text())

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app

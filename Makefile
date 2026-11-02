.PHONY: install test lint ingest-synth ingest demo bench web serve web-dev

install:
	pip install -e ".[dev,api,plot]"

test:
	python -m pytest -q

lint:
	ruff check .

# Deterministic fake-but-realistic ticks; works offline.
ingest-synth:
	python -m mtm.cli ingest-synth --days 5 --trades 200000 --out data/parquet

# Real Binance public trade files (needs network access to data.binance.vision).
ingest:
	python -m mtm.cli ingest-binance --symbols BTCUSDT ETHUSDT --start 2025-01-02 --end 2025-01-06 --out data/parquet

# Run Q1-Q3 on every engine and print timings.
demo:
	python -m mtm.cli demo --root data/parquet --symbol BTCUSDT

# Rebuild datasets and rerun the full benchmark (about 5 minutes for 1M and 10M rows).
bench:
	python -m mtm.bench run --sizes 1000000 10000000 --warm 7 --out bench/results/week2_synthetic.json

# Build the web app once, then serve it and the API together on http://127.0.0.1:8000
web:
	cd web && npm ci && npm run build

serve:
	python -m mtm.cli serve --root data/parquet --port 8000

# Hot-reloading frontend on :5173; run `make serve` in another terminal for the API.
web-dev:
	cd web && npm run dev

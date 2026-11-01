.PHONY: install test lint ingest-synth ingest demo

install:
	pip install -e ".[dev]"

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

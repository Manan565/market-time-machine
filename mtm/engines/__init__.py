from mtm.engines.base import Engine, assert_results_equal
from mtm.engines.duckdb_engine import DuckDBEngine, DuckDBTableEngine
from mtm.engines.pandas_engine import PandasEngine
from mtm.engines.reference import ReferenceEngine

# Engines that take part in benchmarks. The reference engine is for correctness only.
ENGINES: dict[str, type] = {
    "pandas": PandasEngine,
    "duckdb": DuckDBEngine,
    "duckdb-mem": DuckDBTableEngine,
}

__all__ = [
    "ENGINES",
    "DuckDBEngine",
    "DuckDBTableEngine",
    "Engine",
    "PandasEngine",
    "ReferenceEngine",
    "assert_results_equal",
]

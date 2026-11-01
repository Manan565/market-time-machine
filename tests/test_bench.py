import pytest

from mtm import bench
from mtm.queries import QUERIES


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    base = tmp_path_factory.mktemp("bench")
    return base, bench.ensure_dataset(base, 50_000)


def test_dataset_has_exact_row_count_and_is_reused(tiny):
    base, ref = tiny
    assert ref.row_count("BTCUSDT") == 50_000
    assert ref.row_count("ETHUSDT") == 30_000
    again = bench.ensure_dataset(base, 50_000)  # second call must not rebuild
    assert again.root == ref.root


def test_rows_that_do_not_split_evenly_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        bench.ensure_dataset(tmp_path, 2_500_001)


def test_worker_reports_every_query_and_engines_agree(tiny, tmp_path):
    base, ref = tiny
    results_dir = tmp_path / "results"
    runs = {
        name: bench.run_worker(name, ref.root, n_warm=2, results_dir=results_dir)
        for name in ("pandas", "duckdb")
    }
    for run in runs.values():
        assert set(run["queries"]) == set(QUERIES)
        assert len(run["queries"]["Q1"]["warm_s"]) == 2
        assert run["queries"]["Q1"]["median_s"] > 0
        assert run["peak_rss_mb"] >= run["rss_after_load_mb"] > 0
    assert "explain" in runs["duckdb"] and "explain" not in runs["pandas"]
    assert all(bench.check_agreement(results_dir, ["pandas", "duckdb"]).values())


def test_machine_info_has_the_fields_the_readme_reports():
    info = bench.machine_info()
    assert {"cpu", "ram_gb", "python", "packages", "logical_cpus"} <= set(info)
    assert {"pandas", "duckdb"} <= set(info["packages"])

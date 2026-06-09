"""Phase 8 — benchmark suite smoke test.

Not a performance assertion (timings vary by machine): this just proves the
suite runs end to end at a tiny size, returns the expected structure, and that
the qualitative ordering the project claims holds even on small data.
"""

import pathlib
import sys

import pytest

_BENCH_DIR = pathlib.Path(__file__).resolve().parent.parent / "benchmarks"
sys.path.insert(0, str(_BENCH_DIR))

import benchmark_suite  # noqa: E402  (path inserted above)


def test_run_benchmarks_returns_expected_shape():
    results = benchmark_suite.run_benchmarks(n=300, seed=1)
    assert results["n"] == 300
    for category in ("insert", "point_lookup", "range_scan", "wal"):
        assert category in results
        assert results[category]  # non-empty
        assert all(v >= 0 for v in results[category].values())


def test_index_beats_seqscan_even_small():
    results = benchmark_suite.run_benchmarks(n=500, seed=2)
    pl = results["point_lookup"]
    assert pl["hash"] > pl["seqscan"]
    assert pl["btree"] > pl["seqscan"]


def test_make_charts_writes_png_when_matplotlib_available(tmp_path):
    results = benchmark_suite.run_benchmarks(n=200, seed=3)
    produced = benchmark_suite.make_charts(results, tmp_path)
    if not produced:
        pytest.skip("matplotlib not installed")
    assert (tmp_path / "benchmarks.png").exists()


def test_write_report_creates_markdown(tmp_path):
    results = benchmark_suite.run_benchmarks(n=200, seed=4)
    benchmark_suite.write_report(results, tmp_path, charts=False)
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "QueryX Benchmark Report" in report
    assert "Point lookup" in report

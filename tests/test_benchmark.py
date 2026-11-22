"""Audit benchmark methodology, quality gates, and honest README rendering."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.benchmark import compare, main, profile_settings
from kagent.config import Settings
from scripts.update_readme import update


def test_profiles_do_not_inherit_optimized_defaults() -> None:
    base = Settings(_env_file=None, profile="optimized")
    assert profile_settings(base, "baseline").top_k == 4
    assert profile_settings(base, "baseline").opt_cache is False
    assert profile_settings(base, "optimized").top_k == 3


def test_benchmark_smoke_and_methodology(
    tmp_path: Path, sqlite_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("BENCH_LATENCY_N", "10")
    from eval.benchmark import ingest_docs

    with patch("eval.benchmark.ingest_docs", wraps=ingest_docs) as ingest:
        assert main(["--provider", "fake", "--limit", "20", "--out-dir", str(tmp_path)]) == 0
    ingest.assert_called_once()
    reports = [
        json.loads((tmp_path / f"{name}.json").read_text()) for name in ("baseline", "optimized")
    ]
    for report in reports:
        assert report["count"] == 20
        assert len(report["cold_runs"]) == 3
        assert report["metadata"]["workers_latency"] == 1
        assert report["metadata"]["warmup_count"] == 5
        assert len(report["metadata"]["subset_ids"]) == 10
        assert all(run["count"] == 10 and run["cache_hit_rate"] == 0 for run in report["cold_runs"])
    assert reports[0]["metadata"]["subset_ids"] == reports[1]["metadata"]["subset_ids"]
    assert reports[0]["metadata"]["top_k"] == 4 and reports[1]["metadata"]["top_k"] == 3
    comparison = json.loads((tmp_path / "comparison.json").read_text())
    assert comparison["passed"] and comparison["provider"] == "fake"
    assert (tmp_path / "comparison.md").exists()
    assert reports[1]["repeat_traffic"]["count"] == 3
    damaged = dict(reports[1], overall_accuracy=reports[0]["overall_accuracy"] - 0.03)
    assert not compare(reports[0], damaged, 0.02)["passed"]
    assert compare(reports[0], reports[1], 0.02)["mean_estimated_cost_usd"]["percent"] is None


def test_readme_only_from_real_results(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    original = "Before\n<!-- RESULTS:START -->\nPending\n<!-- RESULTS:END -->\nAfter\n"
    readme.write_text(original)
    assert not update(readme, tmp_path)
    assert readme.read_text() == original
    baseline = {
        "provider": "azure",
        "overall_accuracy": 0.8,
        "latency_seconds": {"mean": 1.234567899, "p95": 2},
        "mean_tokens_per_request": 100,
        "mean_estimated_cost_usd": 0.000010001234,
        "metadata": {"commit": "test-commit"},
    }
    optimized = {
        **baseline,
        "latency_seconds": {"mean": 0.9, "p95": 1.5},
        "repeat_traffic": {"cache_hit_rate": 0.5, "latency_seconds": {"mean": 0.01}},
    }
    comparison = compare(baseline, optimized, 0.02)
    for name, value in [
        ("baseline", baseline),
        ("optimized", optimized),
        ("comparison", comparison),
    ]:
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    assert update(readme, tmp_path)
    first = readme.read_text()
    assert "1.2345678" in first and "test-commit" in first
    assert (
        "repeat-traffic" in first and "estimate" in first and "Before" in first and "After" in first
    )
    update(readme, tmp_path)
    assert readme.read_text() == first
    comparison["provider"] = "fake"
    (tmp_path / "comparison.json").write_text(json.dumps(comparison))
    assert not update(readme, tmp_path)
    assert readme.read_text() == first

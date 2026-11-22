"""Exercise scoring, graph execution, reports, thresholds, and provider retries offline."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from openai import RateLimitError

from eval.run import main, rows_equal, score_answer
from kagent.llm.fakes import FakeLLM
from kagent.llm.retry import rate_limit_retry


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ([[1, "x"], [2, "y"]], [[2.0, "y"], [1.0000005, "x"]], True),
        ([[1], [1]], [[1], [2]], False),
        ([[1]], [[1], [1]], False),
        ([[1]], [[1.000002]], False),
        ([[None, "a"]], [[None, "a"]], True),
        ([["1"]], [[1]], False),
        ([[1]], [[1, 2]], False),
        ([], [], True),
        ([[0.0000009], [0.0]], [[0.0], [0.0000018]], True),
    ],
)
def test_row_comparison(left: list, right: list, expected: bool) -> None:
    assert rows_equal(left, right) is expected


def test_scoring(sqlite_path: Path) -> None:
    chat = FakeLLM()
    question = {"route": "sql", "gold_sql": "SELECT COUNT(*) FROM employees"}
    assert score_answer(
        question,
        {"sql": "SELECT COUNT(*) FROM employees", "answer": "wrong"},
        sqlite_path,
        "fake",
        chat,
    )
    assert not score_answer(question, {"sql": "SELECT 0"}, sqlite_path, "fake", chat)
    assert not score_answer(question, {"sql": "DROP TABLE employees"}, sqlite_path, "fake", chat)
    assert not score_answer(
        question, {"sql": "SELECT * FROM nonexistent"}, sqlite_path, "fake", chat
    )
    assert not score_answer(question, {"sql": None}, sqlite_path, "fake", chat)
    doc = {
        "route": "doc",
        "question": "Leave?",
        "expected_answer": "25 days",
        "must_contain": ["25", "DAYS"],
    }
    assert score_answer(doc, {"answer": "25 days"}, sqlite_path, "fake", chat)
    assert not score_answer(doc, {"answer": "25 years"}, sqlite_path, "fake", chat)
    assert not score_answer(
        doc, {"answer": "25 days", "error": "failure"}, sqlite_path, "fake", chat
    )
    judge = Mock(spec=FakeLLM)
    judge.invoke.return_value = "INCORRECT"
    assert not score_answer(doc, {"answer": "25 days"}, sqlite_path, "azure", judge)
    judge.invoke.return_value = "CORRECT"
    assert score_answer(doc, {"answer": "25 days"}, sqlite_path, "azure", judge)
    judge.invoke.return_value = "CORRECT or INCORRECT"
    assert not score_answer(doc, {"answer": "25 days"}, sqlite_path, "azure", judge)
    vague = {"route": "clarify"}
    assert score_answer(
        vague, {"route": "clarify", "answer": "Which policy?"}, sqlite_path, "fake", chat
    )
    assert not score_answer(
        vague, {"route": "doc", "answer": "Which policy?"}, sqlite_path, "fake", chat
    )
    assert not score_answer(
        vague, {"route": "clarify", "answer": "Please clarify."}, sqlite_path, "fake", chat
    )


@pytest.mark.parametrize("threshold,code", [("1.01", 1), ("0.0", 0)])
def test_cli(
    threshold: str, code: int, sqlite_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    monkeypatch.setattr(
        "sys.argv",
        [
            "eval.run",
            "--provider",
            "fake",
            "--limit",
            "20",
            "--workers",
            "4",
            "--out",
            str(output),
            "--min-accuracy",
            threshold,
        ],
    )
    assert main() == code
    report = json.loads(output.read_text())
    assert report["count"] == 20
    assert {
        "overall_accuracy",
        "per_route_accuracy",
        "routing_accuracy",
        "latency_seconds",
        "error_count",
        "worst_failures",
        "results",
        "passed",
    } <= report.keys()
    assert len(report["results"]) == 20
    assert set(report["per_route_accuracy"]) == {"doc", "sql", "clarify"}
    assert set(report["latency_seconds"]) == {"p50", "p95", "mean"}
    assert len(report["worst_failures"]) <= 10
    assert "Overall accuracy" in step_summary.read_text()
    for item in report["results"]:
        assert {"route", "answer", "sql", "latency_seconds", "error"} <= item.keys()
        assert item["latency_seconds"] >= 0


def test_rate_limit_retry() -> None:
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    error = RateLimitError("rate limited", response=response, body=None)
    call = Mock(side_effect=[error, error, "ok"])
    with patch("kagent.llm.retry.time.sleep") as sleep:
        assert rate_limit_retry(call) == "ok"
    assert [item.args[0] for item in sleep.call_args_list] == [1, 2]
    with patch("kagent.llm.retry.time.sleep"), pytest.raises(RateLimitError):
        rate_limit_retry(Mock(side_effect=error))
    with patch("kagent.llm.retry.time.sleep") as sleep, pytest.raises(ValueError):
        rate_limit_retry(Mock(side_effect=ValueError("not transient")))
    sleep.assert_not_called()

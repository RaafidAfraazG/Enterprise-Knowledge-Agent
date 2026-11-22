"""Paired profile benchmark; real Azure results are required for published claims."""

import argparse
import json
import math
import os
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from eval.run import ROOT, make_report, percentile, score_answer
from kagent.config import Settings
from kagent.data import make_data
from kagent.graph.build import build_graph
from kagent.llm.factory import get_chat_model, get_embedder
from kagent.observability.metrics import request_metrics
from kagent.observability.reporting import markdown
from kagent.observability.tracing import make_tracer
from kagent.retrieval.ingest import ingest_docs, make_client

FLAGS = ("opt_router_model", "opt_cache", "opt_parallel", "opt_top_k", "opt_short_prompts")


def profile_settings(base: Settings, profile: str) -> Settings:
    """Resolve fresh profile defaults; the benchmark baseline always disables flags."""
    values = base.model_dump(exclude=set(FLAGS) | {"profile", "top_k"})
    values["profile"] = profile
    if profile == "baseline":
        values.update({flag: False for flag in FLAGS})
    if "top_k" in base.model_fields_set:
        values["top_k"] = base.top_k
    return Settings(_env_file=None, **values)


def request(graph: Any, case: dict[str, Any], profile: str, tracer: Any) -> dict[str, Any]:
    """Time only graph work; judging and SQL comparison are excluded from latency."""
    prediction: dict[str, Any] = {}
    with request_metrics(case["question"], profile, tracer) as recorder:
        try:
            prediction = graph.invoke({"question": case["question"]})
        except Exception as exc:
            prediction["error"] = f"Graph failed ({type(exc).__name__})"
        recorder.finish(prediction)
    return {
        "id": case["id"],
        "question": case["question"],
        "expected_route": case["route"],
        "expected_answer": case["expected_answer"],
        "route": prediction.get("route"),
        "answer": prediction.get("answer", ""),
        "sql": prediction.get("sql"),
        "error": prediction.get("error"),
        "routing_correct": prediction.get("route") == case["route"],
        "usage_calls": [
            {
                key: event[key]
                for key in (
                    "name",
                    "kind",
                    "model",
                    "deployment",
                    "prompt_tokens",
                    "completion_tokens",
                    "estimated_cost_usd",
                    "cache_hit",
                )
            }
            for event in recorder.events
            if event["kind"] in {"llm", "embedding"}
        ],
        "metrics": recorder.summary(),
        "latency_seconds": recorder.summary()["latency_seconds"],
    }


def traffic_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a single clearly separated traffic scenario."""
    times = [row["latency_seconds"] for row in records]
    return dict(
        count=len(records),
        latency_seconds={
            "mean": statistics.mean(times) if times else 0,
            "p50": percentile(times, 0.5),
            "p95": percentile(times, 0.95),
        },
        mean_tokens_per_request=statistics.mean(row["metrics"]["total_tokens"] for row in records)
        if records
        else 0,
        mean_estimated_cost_usd=statistics.mean(
            row["metrics"]["estimated_cost_usd"] for row in records
        )
        if records
        else 0,
        cache_hit_rate=sum(row["metrics"]["cache_hit"] for row in records) / len(records)
        if records
        else 0,
        error_count=sum(bool(row["error"]) for row in records),
        results=records,
    )


def changes(before: float, after: float) -> dict[str, float | None]:
    """Undefined percentage changes from zero are null, never fabricated."""
    return {
        "absolute": after - before,
        "percent": (after - before) / before * 100 if before else None,
    }


def compare(
    baseline: dict[str, Any], optimized: dict[str, Any], maximum_drop: float
) -> dict[str, Any]:
    """Compare headline cold passes and apply the accuracy regression gate."""
    delta = optimized["overall_accuracy"] - baseline["overall_accuracy"]
    result: dict[str, Any] = {
        "provider": baseline["provider"],
        "accuracy_delta_pp": delta * 100,
        "max_accuracy_drop": maximum_drop,
        "passed": delta >= -maximum_drop or math.isclose(delta, -maximum_drop, abs_tol=1e-12),
        "baseline_accuracy": baseline["overall_accuracy"],
        "optimized_accuracy": optimized["overall_accuracy"],
    }
    for name, before, after in [
        (
            "mean_latency_seconds",
            baseline["latency_seconds"]["mean"],
            optimized["latency_seconds"]["mean"],
        ),
        (
            "p95_latency_seconds",
            baseline["latency_seconds"]["p95"],
            optimized["latency_seconds"]["p95"],
        ),
        (
            "mean_tokens_per_request",
            baseline["mean_tokens_per_request"],
            optimized["mean_tokens_per_request"],
        ),
        (
            "mean_estimated_cost_usd",
            baseline["mean_estimated_cost_usd"],
            optimized["mean_estimated_cost_usd"],
        ),
    ]:
        result[name] = changes(before, after)
    return result


def main(argv: list[str] | None = None) -> int:
    """Run profiles on one dataset and collection, saving raw measurements and comparison."""
    defaults = Settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["fake", "azure"], default=defaults.llm_provider)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out-dir", type=Path, default=Path("eval/results"))
    args = parser.parse_args(argv)
    latency_n = int(os.getenv("BENCH_LATENCY_N", "100"))
    maximum_drop = float(os.getenv("BENCH_MAX_ACCURACY_DROP", "0.02"))
    if args.workers < 1 or latency_n < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers, latency subset size, and limit must be positive")
    if not math.isfinite(maximum_drop) or not 0 <= maximum_drop <= 1:
        parser.error("BENCH_MAX_ACCURACY_DROP must be between 0 and 1")
    cases = [json.loads(line) for line in (ROOT / "eval/questions.jsonl").read_text().splitlines()]
    if args.limit:
        cases = cases[: args.limit]
    base = defaults.model_copy(update={"llm_provider": args.provider})
    if args.provider == "fake":
        base = base.model_copy(update={"qdrant_url": ""})
    if args.provider == "azure" and not base.qdrant_collection.startswith("eval_"):
        parser.error("Use a dedicated QDRANT_COLLECTION starting eval_ for Azure benchmarks")
    db = Path(base.sqlite_path)
    if not db.exists():
        make_data(db)
    chat, embedder = get_chat_model(base), get_embedder(base)
    client = make_client(base)
    tracer = make_tracer(base)
    subset = random.Random(42).sample(cases, min(latency_n, len(cases)))
    reports = {}
    try:
        ingest_docs(ROOT / "data/docs", embedder, client, base.qdrant_collection)
        for profile in ("baseline", "optimized"):
            settings = profile_settings(base, profile)
            graph = build_graph(
                chat, embedder, client, db, collection=base.qdrant_collection, settings=settings
            )

            def accuracy(case: dict[str, Any]) -> dict[str, Any]:
                row = request(graph, case, profile, tracer)
                try:
                    row["answer_correct"] = score_answer(case, row, db, args.provider, chat)
                except Exception as exc:
                    row["answer_correct"] = False
                    row["error"] = f"Scoring failed ({type(exc).__name__})"
                return row

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                records = list(pool.map(accuracy, cases))
            report = make_report(records, args.provider)
            cold_runs = []
            for _ in range(3):
                graph.clear_caches()
                for case in cases[:5]:
                    request(graph, case, profile, tracer)
                # Exclude warm-up, including its answer/embedding/schema cache effects.
                graph.clear_caches()
                cold_runs.append(
                    traffic_summary([request(graph, case, profile, tracer) for case in subset])
                )
            report["accuracy_latency_seconds"] = report.pop("latency_seconds")
            report["latency_seconds"] = {
                metric: statistics.median(run["latency_seconds"][metric] for run in cold_runs)
                for metric in ("mean", "p50", "p95")
            }
            report["mean_tokens_per_request"] = statistics.median(
                run["mean_tokens_per_request"] for run in cold_runs
            )
            report["mean_estimated_cost_usd"] = statistics.median(
                run["mean_estimated_cost_usd"] for run in cold_runs
            )
            report["cold_runs"] = cold_runs
            report["profile"] = profile
            report["metadata"] = {
                "subset_ids": [case["id"] for case in subset],
                "seed": 42,
                "commit": os.getenv("GITHUB_SHA", "not recorded"),
                "actions_run_id": os.getenv("GITHUB_RUN_ID", "not recorded"),
                "workers_accuracy": args.workers,
                "workers_latency": 1,
                "warmup_count": min(5, len(cases)),
                "latency_runs": 3,
                "flags": {flag: getattr(settings, flag) for flag in FLAGS},
                "top_k": settings.top_k,
                "collection": base.qdrant_collection,
                "chat_deployment": base.azure_openai_chat_deployment,
                "router_deployment": base.azure_openai_router_deployment,
                "embedding_deployment": base.azure_openai_embed_deployment,
            }
            if profile == "optimized":
                graph.clear_caches()
                first = [request(graph, case, profile, tracer) for case in subset]
                replay = subset[: max(1, int(len(subset) * 0.3))]
                report["repeat_traffic"] = traffic_summary(
                    [request(graph, case, profile, tracer) for case in replay]
                )
                report["repeat_traffic"]["initial_pass"] = traffic_summary(first)
                report["repeat_traffic"]["replay_ids"] = [case["id"] for case in replay]
            reports[profile] = report
        comparison = compare(reports["baseline"], reports["optimized"], maximum_drop)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name, report in {**reports, "comparison": comparison}.items():
            (args.out_dir / f"{name}.json").write_text(
                json.dumps(report, indent=2, allow_nan=False) + "\n"
            )
        text = markdown(reports["baseline"], reports["optimized"], comparison)
        (args.out_dir / "comparison.md").write_text(text)
        print(text)
        if path := os.getenv("GITHUB_STEP_SUMMARY"):
            with Path(path).open("a") as output:
                output.write(text)
        return 0 if comparison["passed"] else 1
    finally:
        tracer.flush()
        tracer.close() if hasattr(tracer, "close") else None
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

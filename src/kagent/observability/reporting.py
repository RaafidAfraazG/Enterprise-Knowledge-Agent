"""Render measured benchmark results without inventing or rounding up values."""

from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Any


def number(value: float | int | None) -> str:
    """Truncate to eight significant digits; raw JSON retains full precision."""
    if value is None:
        return "N/A"
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise ValueError("Nonfinite measurement")
    if not decimal:
        return "0"
    with localcontext() as context:
        context.prec = max(28, len(decimal.as_tuple().digits) + 8)
        return str(decimal.quantize(Decimal(1).scaleb(decimal.adjusted() - 7), rounding=ROUND_DOWN))


def markdown(
    baseline: dict[str, Any], optimized: dict[str, Any], comparison: dict[str, Any]
) -> str:
    """Keep cold-work and repeat-traffic scenarios visibly separate."""
    lines = [
        f"# Paired benchmark ({comparison['provider']})",
        "",
        "Fake results are smoke checks, not performance evidence."
        if comparison["provider"] == "fake"
        else "Measured on Azure; cost is an estimate.",
        "",
        "| Metric | Baseline | Optimized | Absolute change | Change (%) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, key, before, after in [
        (
            "Mean cold latency (s)",
            "mean_latency_seconds",
            baseline["latency_seconds"]["mean"],
            optimized["latency_seconds"]["mean"],
        ),
        (
            "P95 cold latency (s)",
            "p95_latency_seconds",
            baseline["latency_seconds"]["p95"],
            optimized["latency_seconds"]["p95"],
        ),
        (
            "Mean tokens/request",
            "mean_tokens_per_request",
            baseline["mean_tokens_per_request"],
            optimized["mean_tokens_per_request"],
        ),
        (
            "Mean estimated USD/request",
            "mean_estimated_cost_usd",
            baseline["mean_estimated_cost_usd"],
            optimized["mean_estimated_cost_usd"],
        ),
    ]:
        change = comparison[key]
        lines.append(
            f"| {label} | {number(before)} | {number(after)} | "
            f"{number(change['absolute'])} | {number(change['percent'])} |"
        )
    lines.extend(
        [
            "",
            "| Accuracy | Baseline | Optimized |",
            "| --- | --- | --- |",
            f"| Overall | {number(baseline['overall_accuracy'])} | "
            f"{number(optimized['overall_accuracy'])} |",
        ]
    )
    if "routing_accuracy" in baseline:
        lines.append(
            f"| Routing | {number(baseline['routing_accuracy'])} | "
            f"{number(optimized['routing_accuracy'])} |"
        )
    for route in baseline.get("per_route_accuracy", {}):
        lines.append(
            f"| {route} | {number(baseline['per_route_accuracy'][route]['accuracy'])} | "
            f"{number(optimized['per_route_accuracy'][route]['accuracy'])} |"
        )
    repeat = optimized["repeat_traffic"]
    lines.extend(
        [
            "",
            f"Accuracy delta: {number(comparison['accuracy_delta_pp'])} percentage points.",
            f"Quality gate passed: {comparison['passed']}.",
            "",
            "## Separate repeat-traffic scenario (optimized only)",
            f"Cache hit rate on repeats: {number(repeat['cache_hit_rate'])}",
            "Mean latency on repeats (s): " + number(repeat["latency_seconds"]["mean"]),
            "",
            "Headline latency uses three cold sequential passes, excluding warm-up and repeats.",
            "Cost is an estimate, not an invoice. Values are truncated; "
            "JSON retains full precision.",
        ]
    )
    return "\n".join(lines) + "\n"

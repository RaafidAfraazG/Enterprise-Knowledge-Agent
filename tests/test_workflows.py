"""Static workflow validation and executable credential gates; no cloud calls."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class UniqueLoader(yaml.BaseLoader):
    """Reject duplicate YAML keys instead of silently discarding a job setting."""


def unique_mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict:
    """Read a mapping and fail on duplicate keys."""
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def workflow() -> dict:
    """Read CI with string-preserving YAML so the 'on' key is not a boolean."""
    return yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=UniqueLoader)


def test_jobs_and_syntax() -> None:
    config = workflow()
    assert set(config["on"]) == {"push", "pull_request"}
    assert config["permissions"] == {"contents": "read"}
    jobs = config["jobs"]
    assert set(jobs) == {"test", "eval", "deploy"}
    assert jobs["eval"]["needs"] == "test"
    assert jobs["deploy"]["needs"] == "eval"
    assert "refs/heads/main" in jobs["deploy"]["if"]
    assert jobs["deploy"]["concurrency"]["cancel-in-progress"] == "false"
    assert jobs["eval"]["env"]["QDRANT_COLLECTION"] == "eval_${{ github.run_id }}"
    assert "secrets" not in jobs["test"].get("env", {})
    for job in jobs.values():
        assert "secrets." not in job.get("if", "")
        for step in job["steps"]:
            if "uses" in step:
                assert step["uses"].rsplit("@", 1)[1].startswith("v")
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
    for name in ("eval", "deploy"):
        steps = jobs[name]["steps"]
        assert steps[0]["id"] == "credentials"
        for step in steps[1:]:
            if step.get("uses", "").startswith("actions/upload-artifact"):
                assert "always()" in step["if"]
            else:
                assert step["if"] == "steps.credentials.outputs.configured == 'true'"
    subprocess.run(["bash", "-n", str(ROOT / "scripts/azure_bootstrap.sh")], check=True)


@pytest.mark.parametrize("job_name", ["eval", "deploy"])
@pytest.mark.parametrize("configured", [False, True])
def test_credential_gates(job_name: str, configured: bool, tmp_path: Path) -> None:
    job = workflow()["jobs"][job_name]
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    environment = {
        "PATH": os.defpath,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    if configured:
        environment.update({key: "test-placeholder" for key in job["env"]})
    process = subprocess.run(
        ["bash", "-eu", "-c", job["steps"][0]["run"]],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0
    assert f"configured={str(configured).lower()}" in output.read_text()
    assert "test-placeholder" not in process.stdout
    if not configured:
        assert f"skipping {job_name}" in summary.read_text()


def test_docker_static_contract() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert text.startswith("FROM python:3.11-slim")
    assert "USER kagent" in text and "HEALTHCHECK" in text
    assert "scripts/make_data.py" in text and "${PORT:-8000}" in text
    assert "0.0.0.0" in text and "EXPOSE 8000" in text


def test_benchmark_workflow_and_gates(tmp_path: Path) -> None:
    config = yaml.load((ROOT / ".github/workflows/benchmark.yml").read_text(), Loader=UniqueLoader)
    assert "workflow_dispatch" in config["on"]
    job = config["jobs"]["benchmark"]
    assert job["env"]["QDRANT_COLLECTION"].startswith("eval_bench_")
    assert config["permissions"] == {"contents": "read"}
    for step in job["steps"]:
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
        if step is not job["steps"][0]:
            assert "steps.credentials.outputs.configured == 'true'" in step["if"]
    for configured in (False, True):
        output, summary = tmp_path / "output", tmp_path / "summary"
        output.unlink(missing_ok=True)
        environment = {
            "PATH": os.defpath,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        if configured:
            environment.update({name: "placeholder" for name in job["env"]})
        result = subprocess.run(
            ["bash", "-eu", "-c", job["steps"][0]["run"]],
            env=environment,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0
        assert f"configured={str(configured).lower()}" in output.read_text()
        assert "placeholder" not in result.stdout
    assert "skipping benchmark" in summary.read_text()

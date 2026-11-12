"""Production release requires all product gates on the exact main commit."""
import json
import runpy
import subprocess
import time
from pathlib import Path

import pytest

gate = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/verify_worker_release_gates.py"))
SHA = "a" * 40


def run(**changes):
    return {"id": 42, "run_number": 3, "head_sha": SHA, "head_branch": "main", "event": "push",
            "status": "completed", "conclusion": "success", **changes}


def jobs(**changes):
    return [{"name": name, "status": "completed", "conclusion": changes.get(name, "success")}
            for name in ("lint-and-tests", "pdf-golden", "pro-quality")]


def test_requires_exact_commit_completed_main_ci():
    assert gate["select_ci_run"]([run()], SHA)["id"] == 42
    gate["validate_jobs"](jobs())


@pytest.mark.parametrize("changes", [{"head_sha": "b" * 40}, {"head_branch": "feature"},
    {"event": "pull_request"}, {"status": "in_progress"}, {"conclusion": "failure"}])
def test_other_commits_prs_and_unfinished_or_failed_ci_cannot_release(changes):
    with pytest.raises(ValueError):
        gate["select_ci_run"]([run(**changes)], SHA)


def test_newer_failed_run_cannot_reuse_older_success():
    with pytest.raises(ValueError):
        gate["select_ci_run"]([run(), run(id=43, run_number=4, conclusion="failure")], SHA)


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", None])
def test_every_required_job_must_succeed(conclusion):
    with pytest.raises(ValueError):
        gate["validate_jobs"](jobs(**{"pdf-golden": conclusion}))


def test_missing_gate_and_in_progress_job_block_release():
    with pytest.raises(ValueError):
        gate["validate_jobs"](jobs()[:-1])
    incomplete = jobs()
    incomplete[0]["status"] = "in_progress"
    with pytest.raises(ValueError):
        gate["validate_jobs"](incomplete)


@pytest.mark.parametrize("candidate_status", ["success", "failure", "missing", "job_missing", "job_skipped"])
def test_normal_release_requires_the_exact_main_debian_runtime_ci(monkeypatch, candidate_status):
    calls = []

    def api(endpoint, *, paginate=False):
        calls.append(endpoint)
        if "worker-debian-candidate.yml/runs" in endpoint:
            conclusion = "success" if candidate_status.startswith("job_") else candidate_status
            return {"workflow_runs": [] if candidate_status == "missing" else [run(id=77, conclusion=conclusion)]}
        if "workflows/ci.yml/runs" in endpoint:
            return {"workflow_runs": [run()]}
        runtime = [{"name": n, "status": "completed", "conclusion": "success"} for n in ("candidate-tests", "validate-image")]
        if candidate_status == "job_missing":
            runtime.pop()
        elif candidate_status == "job_skipped":
            runtime[-1]["conclusion"] = "skipped"
        return [{"jobs": jobs() if "/42/" in endpoint else runtime}]

    monkeypatch.setenv("GITHUB_REPOSITORY", "claston/gettdone")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("WORKER_IMAGE_SOURCE", "build-main")
    monkeypatch.setitem(gate["main"].__globals__, "api", api)
    if candidate_status != "success":
        with pytest.raises(ValueError):
            gate["main"]()
    else:
        gate["main"]()
    assert any("worker-debian-candidate.yml/runs" in endpoint for endpoint in calls)


def test_historical_digest_promotion_keeps_its_own_evidence_gate(monkeypatch):
    def api(endpoint, *, paginate=False):
        assert "worker-debian-candidate.yml" not in endpoint
        return {"workflow_runs": [run()]} if "workflows/ci.yml" in endpoint else [{"jobs": jobs()}]

    monkeypatch.setenv("GITHUB_REPOSITORY", "claston/gettdone")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("WORKER_IMAGE_SOURCE", "pr488-debian-canary")
    monkeypatch.setitem(gate["main"].__globals__, "api", api)
    gate["main"]()


@pytest.fixture
def github_api_responses(monkeypatch):
    responses, calls, delays = [], [], []

    def check_output(args, **kwargs):
        calls.append((args, kwargs))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(subprocess, "check_output", check_output)
    monkeypatch.setattr(time, "sleep", delays.append)
    return responses, calls, delays


def api_error(status):
    return subprocess.CalledProcessError(
        1, ["gh", "api"], output='[{"jobs": []}]', stderr=f"gh: Server Error (HTTP {status})\n",
    )


@pytest.mark.parametrize("failure", [500, 502, 503, 504, 429, "timeout"])
def test_github_api_retries_transient_failure_and_discards_partial_output(github_api_responses, failure):
    responses, calls, delays = github_api_responses
    error = subprocess.TimeoutExpired(["gh", "api"], 60) if failure == "timeout" else api_error(failure)
    expected = [{"jobs": jobs()}]
    responses.extend([error, json.dumps(expected)])

    assert gate["api"]("repos/example/repo/actions/runs/42/jobs", paginate=True) == expected
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][0][-2:] == ["--paginate", "--slurp"]
    assert calls[0][1]["timeout"] == 60
    assert calls[0][1]["stderr"] == subprocess.PIPE
    assert delays == [2]


@pytest.mark.parametrize("failure", [502, "timeout"])
def test_github_api_stops_after_three_attempts_and_blocks_release(github_api_responses, failure):
    responses, calls, delays = github_api_responses
    error = subprocess.TimeoutExpired(["gh", "api"], 60) if failure == "timeout" else api_error(failure)
    responses.extend([error] * 3)
    with pytest.raises(RuntimeError, match="3 attempts.*Release remains blocked"):
        gate["api"]("repos/example/repo/actions/runs")
    assert len(calls) == 3
    assert delays == [2, 4]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, "unknown"])
def test_github_api_does_not_retry_permanent_or_unknown_errors(github_api_responses, status):
    responses, calls, delays = github_api_responses
    responses.append(api_error(status))
    with pytest.raises(RuntimeError, match="Release remains blocked"):
        gate["api"]("repos/example/repo/actions/runs")
    assert len(calls) == 1
    assert delays == []


def test_github_api_rejects_invalid_json_without_retrying(github_api_responses):
    responses, calls, delays = github_api_responses
    responses.append("not json")
    with pytest.raises(json.JSONDecodeError):
        gate["api"]("repos/example/repo/actions/runs")
    assert len(calls) == 1
    assert delays == []


@pytest.mark.parametrize("product_result", ["success", "skipped"])
def test_release_gates_still_validate_jobs_after_github_502(monkeypatch, github_api_responses, product_result):
    responses, calls, delays = github_api_responses
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setenv("WORKER_IMAGE_SOURCE", "build-main")
    responses.extend([
        json.dumps({"workflow_runs": [run()]}),
        api_error(502),
        json.dumps([{"jobs": jobs(**{"pdf-golden": product_result})}]),
        json.dumps({"workflow_runs": [run(id=77)]}),
        json.dumps([{"jobs": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in ("candidate-tests", "validate-image")
        ]}]),
    ])
    if product_result == "success":
        gate["main"]()
        assert len(calls) == 5
    else:
        with pytest.raises(ValueError, match="pdf-golden"):
            gate["main"]()
        assert len(calls) == 3
    assert delays == [2]

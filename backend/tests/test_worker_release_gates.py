"""Production release requires all product gates on the exact main commit."""
import runpy
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

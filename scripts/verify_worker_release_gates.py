"""Refuse a production worker release until CI passed on its exact main SHA."""
import json
import os
import re
import subprocess

REQUIRED_JOBS = {"lint-and-tests", "pdf-golden", "pro-quality"}


def select_ci_run(runs, sha):
    matches = [run for run in runs if run.get("head_sha") == sha and run.get("head_branch") == "main"
               and run.get("event") == "push"]
    if not matches:
        raise ValueError("No push/main CI run exists for this exact release commit.")
    latest = max(matches, key=lambda run: (run["run_number"], run.get("run_attempt", 1)))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("The latest CI run for this release commit has not passed. Wait for or repair CI before deploying.")
    return latest


def validate_jobs(jobs):
    passed = {job["name"] for job in jobs if job.get("status") == "completed" and job.get("conclusion") == "success"}
    missing = REQUIRED_JOBS - passed
    if missing:
        raise ValueError("Required release gates did not pass: " + ", ".join(sorted(missing)))


def api(endpoint, *, paginate=False):
    args = ["gh", "api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(args, text=True, timeout=60))


def main():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid release repository or commit.")
    runs = api(f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&branch=main&event=push&per_page=100")
    run = select_ci_run(runs["workflow_runs"], sha)
    pages = api(f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100", paginate=True)
    validate_jobs([job for page in pages for job in page["jobs"]])
    print(f"Release gates passed for {sha}: {', '.join(sorted(REQUIRED_JOBS))}; CI run {run['id']}.")


if __name__ == "__main__":
    main()

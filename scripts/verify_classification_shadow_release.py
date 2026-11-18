"""Use the existing conversion release's product gates without its image build."""
import os
import re

from verify_worker_release_gates import REQUIRED_JOBS, api, select_ci_run, validate_jobs


def main():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    if (os.environ.get("GITHUB_REF") != "refs/heads/main"
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", sha)):
        raise ValueError("Fiscal release requires an exact main commit")
    runs = api(f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&branch=main&event=push&per_page=100")
    run = select_ci_run(runs["workflow_runs"], sha)
    pages = api(f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100", paginate=True)
    validate_jobs([job for page in pages for job in page["jobs"]])
    print(f"Product gates passed for {sha}: {', '.join(sorted(REQUIRED_JOBS))}; run {run['id']}.")


if __name__ == "__main__":
    main()

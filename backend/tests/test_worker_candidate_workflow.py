from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_cannot_publish_or_deploy_and_keeps_security_gate():
    workflow = (ROOT / ".github/workflows/worker-debian-candidate.yml").read_text(encoding="utf-8")
    assert "push: false" in workflow
    assert "id-token: write" not in workflow
    assert "secrets." not in workflow
    assert "aws-actions/" not in workflow
    assert "cloudformation" not in workflow
    assert "--pkg-types os,library" in workflow
    assert "--severity HIGH,CRITICAL --exit-code 1" in workflow
    assert "--ignore-unfixed" not in workflow
    assert "continue-on-error" not in workflow
    assert workflow.index("--exit-code 1") < workflow.index('docker save "$CANDIDATE_IMAGE"')


def test_candidate_preserves_handler_architecture_and_restricted_context():
    candidate = (ROOT / "Dockerfile.lambda.debian").read_text(encoding="utf-8")
    assert 'CMD ["app.workers.conversion_lambda.lambda_handler"]' in candidate
    assert 'ENTRYPOINT ["/usr/local/bin/python3.12", "-m", "awslambdaric"]' in candidate
    assert "python:3.12-slim-trixie@sha256:" in candidate
    assert "--only-binary=:all:" in candidate
    assert '"-m", "app.workers.worker_image_contract"' in candidate
    assert "gcr.io/distroless/cc-debian13:nonroot@sha256:" in candidate
    assert "COPY --from=build /runtime-status/ /var/lib/dpkg/status.d/" in candidate
    original = set((ROOT / "Dockerfile.lambda.dockerignore").read_text(encoding="utf-8").splitlines())
    separate = set((ROOT / "Dockerfile.lambda.debian.dockerignore").read_text(encoding="utf-8").splitlines())
    assert original - {"!Dockerfile.lambda"} <= separate


def test_worker_debian_ci_tests_the_canonical_production_image_on_every_main_push():
    workflow = (ROOT / ".github/workflows/worker-debian-candidate.yml").read_text(encoding="utf-8")
    assert 'branches: [main, "feat/worker-debian-candidate"]' in workflow
    assert "file: Dockerfile.lambda\n" in workflow
    assert "file: Dockerfile.lambda.debian" not in workflow
    assert "-r backend/requirements-worker.txt" in workflow
    assert 'Path("backend/requirements-worker.txt")' in workflow

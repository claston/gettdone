import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy-worker-ecr.yml"


def _workflow() -> str:
    assert WORKFLOW_PATH.is_file(), "worker ECR deployment workflow is missing"
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_worker_deploy_is_manual_confirmed_and_main_only():
    workflow = _workflow()

    assert "workflow_dispatch:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "inputs.deploy" in workflow
    assert "\n  push:" not in workflow
    assert "\n  workflow_run:" not in workflow
    assert "environment: production-worker" in workflow


def test_worker_deploy_uses_oidc_and_pinned_actions_without_long_lived_secrets():
    workflow = _workflow()

    assert "id-token: write" in workflow
    assert "contents: read" in workflow
    assert "vars.AWS_WORKER_DEPLOY_ROLE_ARN" in workflow
    assert "vars.AWS_ACCOUNT_ID" in workflow
    assert "role-duration-seconds: 900" in workflow
    assert "allowed-account-ids: ${{ vars.AWS_ACCOUNT_ID }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert (
        "aws-actions/configure-aws-credentials@"
        "cbe3b392738ccf3f987d68400dafcf4b0624a56c"
    ) in workflow
    assert "docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e" in workflow
    assert "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a" in workflow
    assert "secrets.AWS_" not in workflow
    assert "DATABASE_URL" not in workflow
    assert "ACCESS_CONTROL_TOKEN" not in workflow
    assert "@v" not in workflow


def test_worker_deploy_uses_dedicated_image_only_execution_role():
    workflow = _workflow()

    assert "GettDoneWorkerImageCloudFormationExecutionRole" in workflow
    assert "GettDoneCloudFormationExecutionRole" not in workflow


def test_worker_deploy_blocks_vulnerable_ecr_images():
    workflow = _workflow()

    assert "ecr wait image-scan-complete" in workflow
    assert "ScanNotFoundException" in workflow
    assert "scan_registration_attempt" in workflow
    assert "sleep 5" in workflow
    assert "Timed out waiting for the ECR image scan to be registered" in workflow
    assert "ecr describe-image-scan-findings" in workflow
    assert "findingSeverityCounts" in workflow
    assert "'.CRITICAL // 0'" in workflow
    assert "'.HIGH // 0'" in workflow


def test_worker_deploy_reports_blocking_ecr_findings_before_failing():
    workflow = _workflow()

    assert "imageScanFindings.findings" in workflow
    assert 'select(.severity == "CRITICAL" or .severity == "HIGH")' in workflow
    assert 'select(.key == "package_name")' in workflow
    assert 'select(.key == "package_version")' in workflow
    assert 'select(.key == "fixed_version")' in workflow
    assert "### Blocking findings" in workflow
    assert "Worker image has ${critical} critical and ${high} high vulnerability findings." in workflow


def test_worker_deploy_builds_immutable_lambda_image_and_uses_digest():
    workflow = _workflow()

    assert "file: ./Dockerfile.lambda" in workflow
    assert "platforms: linux/amd64" in workflow
    assert "provenance: false" in workflow
    assert "sbom: false" in workflow
    assert "APP_RELEASE=${{ github.sha }}" in workflow
    assert "release-${GITHUB_SHA::12}" in workflow
    assert "image_uri=\"${repository_uri}@${digest}\"" in workflow
    assert ":latest" not in workflow


def test_worker_deploy_preserves_stack_parameters_and_validates_change_set():
    workflow = _workflow()

    assert "--use-previous-template" in workflow
    assert "UsePreviousValue: true" in workflow
    assert "ConversionImageUri" in workflow
    assert "--include-property-values" in workflow
    assert "LogicalResourceId == \"ConversionWorker\"" in workflow
    assert "ResourceType == \"AWS::Lambda::Function\"" in workflow
    assert "Replacement == \"False\"" in workflow
    assert "cloudformation wait stack-update-complete" in workflow
    assert "EnableQueueTrigger" in workflow
    assert "EnableOutboxDispatcher" in workflow


def test_approved_pr488_promotion_selects_the_canary_digest_without_a_build():
    workflow = _workflow()
    assert "image_source:" in workflow
    assert "default: build-main" in workflow
    assert "pr488-debian-canary" in workflow
    assert "sha256:737cd4fb2e9cc40a92607358378ff408990bd67d4b00064735d0d5827213c11f" in workflow
    assert 'image_exists=true' in workflow
    assert 'The approved PR 488 image is missing or has a different digest' in workflow
    assert 'Unsupported image source' in workflow


def test_approved_promotion_requires_the_exact_candidate_ci_and_preserves_production_approval():
    workflow = _workflow()
    assert "40254942573" in workflow
    assert "adf55e74a24679b9a4aaec2f895fdf7a653f1132" in workflow
    assert '"candidate-tests", "validate-image"' in workflow
    assert "environment: production-worker" in workflow
    assert "scripts/verify_worker_release_gates.py" in workflow


def test_promotion_records_a_durable_rollback_before_executing_cloudformation():
    workflow = _workflow()
    assert "worker-image-rollback-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "worker-image-rollback.json" in workflow
    assert '"previous_image_uri"' in workflow
    assert '"target_image_uri"' in workflow
    assert workflow.index("Record rollback image") < workflow.index("Execute worker image change set")
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow


def test_normal_release_tests_and_scans_the_actual_image_before_aws_and_push():
    workflow = _workflow()
    assert "push: false" in workflow and "load: true" in workflow
    assert "Build Debian worker image locally" in workflow
    assert "scripts/smoke_worker_candidate_image.py" in workflow
    assert "--severity HIGH,CRITICAL --exit-code 1" in workflow
    assert "--pkg-types os,library" in workflow and "--ignore-unfixed" not in workflow
    assert "worker_canary" in workflow and "image: postgres:16" in workflow
    assert workflow.index("Build Debian worker image locally") < workflow.index("Validate Debian runtime")
    assert workflow.index("Validate Debian runtime") < workflow.index("Configure temporary AWS credentials")
    assert workflow.index("Block HIGH and CRITICAL") < workflow.index("Configure temporary AWS credentials")
    assert workflow.index("Block HIGH and CRITICAL") < workflow.index("Publish validated Debian image")
    assert "steps.publish.outputs.digest" in workflow
    assert "WORKER_IMAGE_SOURCE: ${{ inputs.image_source }}" in workflow
    assert 'release-${GITHUB_SHA::12}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}' in workflow


def _step_script(name):
    block = _workflow().split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(block.split("        run: |\n", 1)[1]).strip("\n")


def _python_step(name):
    lines = _step_script(name).splitlines()
    assert lines[0] == "python3 - <<'PY'" and lines[-1] == "PY"
    return compile("\n".join(lines[1:-1]), name, "exec")


@pytest.mark.parametrize("mutation", [None, "sha", "status", "conclusion", "path", "jobs"])
def test_candidate_evidence_executes_and_rejects_other_or_incomplete_runs(monkeypatch, mutation):
    run = {"head_sha": "5d7bf8946f38b95fdd10f0d70437b6c1e7955a72", "status": "completed",
           "conclusion": "success", "path": ".github/workflows/worker-debian-candidate.yml"}
    jobs = [{"name": name, "status": "completed", "conclusion": "success"}
            for name in ("candidate-tests", "validate-image")]
    if mutation == "sha":
        run["head_sha"] = "0" * 40
    elif mutation in {"status", "conclusion", "path"}:
        run[mutation] = "incorrect"
    elif mutation == "jobs":
        jobs[-1]["conclusion"] = "skipped"
    monkeypatch.setenv("GITHUB_REPOSITORY", "claston/gettdone")
    monkeypatch.setattr(subprocess, "check_output", lambda args: json.dumps([{"jobs": jobs}] if "--slurp" in args else run).encode())
    code = _python_step("Verify the approved PR 488 candidate evidence")
    if mutation:
        with pytest.raises(SystemExit):
            exec(code, {})
    else:
        exec(code, {})


@pytest.mark.parametrize("previous", ["repository@sha256:" + "a" * 64, "repository:mutable-tag", ""])
def test_rollback_record_requires_an_immutable_previous_image(monkeypatch, tmp_path, previous):
    for key, value in {"PREVIOUS_IMAGE_URI": previous, "TARGET_IMAGE_URI": "repository@sha256:" + "b" * 64,
                       "WORKER_STACK_NAME": "worker", "AWS_REGION": "us-east-1", "IMAGE_SOURCE": "pr488-debian-canary",
                       "GITHUB_SHA": "c" * 40, "GITHUB_RUN_ID": "42", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    code = _python_step("Record rollback image")
    path = tmp_path / "worker-image-rollback.json"
    if "@sha256:" not in previous:
        with pytest.raises(SystemExit):
            exec(code, {})
        assert not path.exists()
    else:
        exec(code, {})
        record = json.loads(path.read_text())
        assert record["previous_image_uri"] == previous
        assert record["target_image_uri"] == "repository@sha256:" + "b" * 64


@pytest.mark.parametrize("source,present,expected", [
    ("pr488-debian-canary", "yes", True), ("pr488-debian-canary", "no", False),
    ("pr488-debian-canary", "wrong", False), ("unsupported", "yes", False), ("build-main", "yes", True),
])
def test_real_target_shell_selects_only_the_approved_digest_and_never_falls_back_to_build(tmp_path, source, present, expected):
    script = _step_script("Resolve ECR and CloudFormation targets")
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    assert bash, "Bash is required to validate the deployment selection guard"
    output = tmp_path / "outputs"
    stub = r'''
    aws() {
      case "$*" in
        'sts get-caller-identity --query Arn --output text') echo arn:aws:sts::111122223333:assumed-role/deploy/test ;;
        'sts get-caller-identity --query Account --output text') echo 111122223333 ;;
        *ConversionImageRepositoryUri*) echo 111122223333.dkr.ecr.us-east-1.amazonaws.com/foundation-images ;;
        *ConversionImageRepositoryName*) echo foundation-images ;;
        *ConversionImageUri*) echo repository@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
        *EnableQueueTrigger*) echo true ;;
        *EnableOutboxDispatcher*) echo false ;;
        *imageTag=release-*) return 1 ;;
        *imageDigest=sha256:*)
          case "$FAKE_CANDIDATE" in
            yes) echo sha256:737cd4fb2e9cc40a92607358378ff408990bd67d4b00064735d0d5827213c11f ;;
            wrong) echo sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
            *) return 1 ;;
          esac ;;
        *) echo 'Unexpected AWS operation' >&2; return 1 ;;
      esac
    }
    '''
    result = subprocess.run([bash, "-c", stub + script], capture_output=True, text=True, timeout=20,
                            env={**os.environ, "IMAGE_SOURCE": source, "FAKE_CANDIDATE": present,
                                 "AWS_REGION": "us-east-1", "FOUNDATION_STACK_NAME": "foundation",
                                 "WORKER_STACK_NAME": "worker", "GITHUB_SHA": "c" * 40,
                                 "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1",
                                 "GITHUB_OUTPUT": output.as_posix()})
    if not expected:
        assert result.returncode != 0
        assert not output.exists(), "An invalid promotion must not fall back to the build path"
    else:
        assert result.returncode == 0, result.stderr
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        if source == "build-main":
            assert values["image_exists"] == "false"
            assert values["existing_digest"] == ""
            assert values["image_tag"] == "release-cccccccccccc-42-1"
        else:
            assert values["image_exists"] == "true"
            assert values["existing_digest"] == "sha256:737cd4fb2e9cc40a92607358378ff408990bd67d4b00064735d0d5827213c11f"
            assert values["image_tag"] == "candidate-debian-5d7bf8946f38"


@pytest.mark.parametrize("mutation", [None, "os", "os_packages", "python"])
def test_production_scanner_guard_refuses_incomplete_or_wrong_inventory(monkeypatch, tmp_path, mutation):
    report = {"Metadata": {"OS": {"Family": "debian"}}, "Results": [
        {"Class": "os-pkgs", "Packages": [{"Name": "libc6"}]},
        {"Type": "python-pkg", "Packages": [{"Name": "pypdf", "Version": "6.21.2"}]}]}
    if mutation == "os":
        report["Metadata"]["OS"]["Family"] = "unknown"
    elif mutation == "os_packages":
        report["Results"][0]["Packages"] = []
    elif mutation == "python":
        report["Results"][1]["Packages"][0]["Version"] = "5.4.0"
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "artifacts/vulnerabilities.json").write_text(json.dumps(report))
    (tmp_path / "backend/requirements-worker.txt").write_text("pypdf==6.21.2\n")
    source = _step_script("Block HIGH and CRITICAL Debian image vulnerabilities").split("python3 - <<'PY'\n", 1)[1]
    code = compile(source.rsplit("\nPY", 1)[0], "production-scan-guard", "exec")
    monkeypatch.chdir(tmp_path)
    if mutation:
        with pytest.raises(SystemExit):
            exec(code, {})
    else:
        exec(code, {})

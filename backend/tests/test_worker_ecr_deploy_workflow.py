from pathlib import Path

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
    assert "ecr describe-image-scan-findings" in workflow
    assert "findingSeverityCounts" in workflow
    assert "'.CRITICAL // 0'" in workflow
    assert "'.HIGH // 0'" in workflow


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

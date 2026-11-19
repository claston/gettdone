"""A fiscal release may only replace the ZIP in its own existing Lambda stack."""
import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("shadow_deploy", SCRIPTS / "deploy_classification_shadow.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def change_set():
    return {"Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE", "Changes": [{"ResourceChange": {
        "Action": "Modify", "LogicalResourceId": "Worker", "ResourceType": "AWS::Lambda::Function", "Replacement": "False",
        "Scope": ["Properties"], "Details": [{"Target": {"Attribute": "Properties", "Name": "Code",
            "Path": "/Properties/Code/" + key, "AfterValue": value, "RequiresRecreation": "Never"}}
            for key, value in {"S3Key": "classification-shadow/releases/new.zip", "S3ObjectVersion": "v2"}.items()]
    }}]}


CODE = {"S3Bucket": "artifact-bucket", "S3Key": "classification-shadow/releases/new.zip", "S3ObjectVersion": "v2"}


def test_zip_only_change_is_accepted():
    deploy.validate_change_set(change_set(), CODE)


@pytest.mark.parametrize("bad", ["other_resource", "queue", "configuration", "replacement", "extra_change", "wrong_zip", "no_details"])
def test_release_refuses_changes_outside_exact_zip(bad):
    change = change_set()
    resource = change["Changes"][0]["ResourceChange"]
    if bad == "other_resource":
        resource["LogicalResourceId"] = "ConversionWorker"
    elif bad == "queue":
        resource["ResourceType"] = "AWS::Lambda::EventSourceMapping"
    elif bad == "configuration":
        resource["Details"].append({"Target": {"Path": "/Properties/Timeout", "AfterValue": "900"}})
    elif bad == "replacement":
        resource["Replacement"] = "True"
    elif bad == "extra_change":
        change["Changes"].append(copy.deepcopy(change["Changes"][0]))
    elif bad == "wrong_zip":
        resource["Details"][0]["Target"]["AfterValue"] = "other.zip"
    else:
        resource["Details"] = []
    with pytest.raises(ValueError):
        deploy.validate_change_set(change, CODE)


def test_all_parameters_except_zip_coordinates_use_previous_values():
    previous = {"ArtifactBucket": "bucket", "ArtifactKey": "old", "ArtifactVersion": "v1",
                "EnableQueue": "true", "ProducerRoleName": "backend"}
    result = deploy.release_parameters(previous, CODE)
    by_key = {row["ParameterKey"]: row for row in result}
    for name in ("ArtifactBucket", "EnableQueue", "ProducerRoleName"):
        assert by_key[name] == {"ParameterKey": name, "UsePreviousValue": True}
    assert by_key["ArtifactKey"]["ParameterValue"] == CODE["S3Key"]
    assert by_key["ArtifactVersion"]["ParameterValue"] == CODE["S3ObjectVersion"]


def test_failed_change_validation_deletes_pending_change_without_executing():
    cfn = Mock()
    invalid = change_set()
    invalid["Changes"][0]["ResourceChange"]["Details"].append({"Target": {"Path": "/Properties/Role"}})
    cfn.describe_change_set.return_value = invalid
    with pytest.raises(ValueError):
        deploy.review_and_execute(cfn, "classification-shadow-test", CODE)
    cfn.execute_change_set.assert_not_called()
    cfn.delete_change_set.assert_called_once()


def test_release_gates_require_product_main_ci_without_conversion_image_dependency(monkeypatch):
    import verify_classification_shadow_release as gate
    sha = "a" * 40
    monkeypatch.setenv("GITHUB_REPOSITORY", "claston/gettdone")
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    calls = []

    def api(endpoint, **kwargs):
        calls.append(endpoint)
        if "workflows/ci.yml/runs" in endpoint:
            return {"workflow_runs": [{"id": 1, "run_number": 1, "head_sha": sha, "head_branch": "main",
                    "event": "push", "status": "completed", "conclusion": "success"}]}
        return [{"jobs": [{"name": name, "status": "completed", "conclusion": "success"}
                          for name in ("lint-and-tests", "pdf-golden", "pro-quality")]}]

    monkeypatch.setattr(gate, "api", api)
    gate.main()
    assert len(calls) == 2
    assert not any("debian" in call for call in calls)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    with pytest.raises(ValueError):
        gate.main()


def test_policy_adds_no_conversion_writes_or_infrastructure_creation():
    from classification_shadow_deploy_policy import policy
    statements = policy("123456789012")["Statement"]
    text = str(statements)
    assert "gettdone-production-worker" not in text
    assert "iam:CreateRole" not in text and "lambda:UpdateFunctionConfiguration" not in text
    assert "cloudformation:CreateStack" not in text
    assert all(row["Resource"] != "*" for row in statements)
    passing = next(row for row in statements if row["Action"] == "iam:PassRole")
    assert passing["Resource"].endswith("/gettdone/GettDoneClassificationCloudFormationExecutionRole")
    assert passing["Condition"]["StringEquals"]["iam:PassedToService"] == "cloudformation.amazonaws.com"


@pytest.mark.parametrize("damage", ["commit", "hash", "source", "tests"])
def test_artifact_from_other_commit_modified_zip_or_failed_tests_is_rejected(tmp_path, damage):
    import hashlib
    import json
    content = b"synthetic zip"
    (tmp_path / "receita-shadow.zip").write_bytes(content)
    evidence = json.loads((SCRIPTS.parent / "receita-worker/upstream.json").read_text())
    evidence.update({"application_commit": "a" * 40, "zip_sha256": hashlib.sha256(content).hexdigest(),
                     "upstream_tests": "passed", "adapter_tests": "passed", "packaged_smoke": "passed"})
    if damage == "commit":
        evidence["application_commit"] = "b" * 40
    elif damage == "hash":
        (tmp_path / "receita-shadow.zip").write_bytes(b"changed")
    elif damage == "source":
        evidence["commit"] = "c" * 40
    else:
        evidence["packaged_smoke"] = "failed"
    (tmp_path / "evidence.json").write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="Artifact"):
        deploy.checked_artifact(tmp_path, "a" * 40)


@pytest.mark.parametrize("outcome", ["success", "queue_changed", "code_mismatch", "unversioned", "wrong_function"])
def test_complete_release_preserves_queue_and_checks_actual_lambda_hash(tmp_path, monkeypatch, outcome):
    import base64
    import hashlib
    import json
    account, sha, content = "123456789012", "a" * 40, b"synthetic package"
    checksum = hashlib.sha256(content).hexdigest()
    evidence = json.loads((SCRIPTS.parent / "receita-worker/upstream.json").read_text())
    evidence.update({"application_commit": sha, "zip_sha256": checksum,
                     "upstream_tests": "passed", "adapter_tests": "passed", "packaged_smoke": "passed"})
    (tmp_path / "evidence.json").write_text(json.dumps(evidence))
    (tmp_path / "receita-shadow.zip").write_bytes(content)
    monkeypatch.setattr(sys, "argv", ["deploy", "--artifacts", str(tmp_path)])
    monkeypatch.setenv("AWS_ACCOUNT_ID", account)
    monkeypatch.setenv("GITHUB_SHA", sha)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    bucket = f"gettdone-classification-artifacts-{account}-us-east-1"
    previous = {"ArtifactBucket": bucket, "ArtifactKey": "old.zip", "ArtifactVersion": "old-version", "EnableQueue": "true"}
    key = f"classification-shadow/releases/{checksum}.zip"
    expected = {**previous, "ArtifactKey": key, "ArtifactVersion": "new-version"}
    if outcome == "queue_changed":
        expected["EnableQueue"] = "false"

    def stack(values):
        return {"Stacks": [{"StackStatus": "UPDATE_COMPLETE", "Parameters": [
            {"ParameterKey": k, "ParameterValue": v} for k, v in values.items()],
            "Outputs": [{"OutputKey": "FunctionName", "OutputValue":
                         "conversion-worker" if outcome == "wrong_function" else deploy.FUNCTION}]}]}

    cfn, s3, functions = Mock(), Mock(), Mock()
    cfn.describe_stacks.side_effect = [stack(previous), stack(expected)]
    change = change_set()
    details = change["Changes"][0]["ResourceChange"]["Details"]
    details[0]["Target"]["AfterValue"] = key
    details[1]["Target"]["AfterValue"] = "new-version"
    cfn.describe_change_set.return_value = change
    s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    s3.put_object.return_value = {} if outcome == "unversioned" else {"VersionId": "new-version"}
    functions.get_function_configuration.side_effect = [
        {"CodeSha256": "old"}, {"CodeSha256": "wrong" if outcome == "code_mismatch" else
         base64.b64encode(hashlib.sha256(content).digest()).decode(), "State": "Active", "LastUpdateStatus": "Successful"}]
    monkeypatch.setattr(deploy.boto3, "client", lambda service, **kwargs:
                        {"cloudformation": cfn, "s3": s3, "lambda": functions}[service])
    if outcome == "success":
        deploy.main()
        request = cfn.create_change_set.call_args.kwargs
        assert request["UsePreviousTemplate"] and request["ChangeSetType"] == "UPDATE"
        assert {"ParameterKey": "EnableQueue", "UsePreviousValue": True} in request["Parameters"]
        assert json.loads((tmp_path / "deployment.json").read_text())["previous_code"]["S3ObjectVersion"] == "old-version"
        cfn.execute_change_set.assert_called_once()
    else:
        with pytest.raises(ValueError):
            deploy.main()
        assert not (tmp_path / "deployment.json").exists()
        if outcome in {"unversioned", "wrong_function"}:
            cfn.create_change_set.assert_not_called()
        else:
            assert (tmp_path / "deployment-before.json").exists()

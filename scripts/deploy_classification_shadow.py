"""Release a tested ZIP through a code-only change set on the existing fiscal stack."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import boto3

STACK = "gettdone-classification-shadow"
FUNCTION = "gettdone-classification-shadow-worker"
REGION = "us-east-1"
EXECUTION_ROLE = "gettdone/GettDoneClassificationCloudFormationExecutionRole"


def release_parameters(previous, code):
    if not {"ArtifactBucket", "ArtifactKey", "ArtifactVersion"} <= previous.keys():
        raise ValueError("Fiscal stack is missing its artifact parameters")
    updates = {"ArtifactKey": code["S3Key"], "ArtifactVersion": code["S3ObjectVersion"]}
    return [{"ParameterKey": key, **({"ParameterValue": updates[key]} if key in updates else {"UsePreviousValue": True})}
            for key in previous]


def validate_change_set(change, code):
    if change.get("Status") != "CREATE_COMPLETE" or change.get("ExecutionStatus") != "AVAILABLE":
        raise ValueError("Fiscal change set is not ready")
    changes = change.get("Changes", [])
    if len(changes) != 1:
        raise ValueError("Release must modify only the fiscal Lambda code")
    resource = changes[0].get("ResourceChange", {})
    if (resource.get("Action") != "Modify" or resource.get("LogicalResourceId") != "Worker"
            or resource.get("ResourceType") != "AWS::Lambda::Function" or resource.get("Replacement") != "False"
            or resource.get("Scope") != ["Properties"]):
        raise ValueError("Release cannot replace resources or change infrastructure")
    seen = set()
    for detail in resource.get("Details", []):
        target = detail.get("Target", {})
        if (target.get("Attribute") != "Properties" or target.get("Name") != "Code"
                or target.get("RequiresRecreation") != "Never"):
            raise ValueError("Change set contains non-code changes")
        path = target.get("Path", "")
        if path == "/Properties/Code":
            value = target.get("AfterValue")
            if isinstance(value, str):
                value = json.loads(value)
            if value != code:
                raise ValueError("Unexpected Lambda code coordinates")
            seen.update({"S3Key", "S3ObjectVersion"})
        else:
            field = path.removeprefix("/Properties/Code/")
            if field not in {"S3Key", "S3ObjectVersion"} or target.get("AfterValue") != code[field]:
                raise ValueError("Change set points to unexpected code or configuration")
            seen.add(field)
    if seen != {"S3Key", "S3ObjectVersion"}:
        raise ValueError("Change set did not prove the exact versioned ZIP update")


def review_and_execute(cfn, name, code, evidence_dir=None):
    try:
        cfn.get_waiter("change_set_create_complete").wait(
            StackName=STACK, ChangeSetName=name, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
        change = cfn.describe_change_set(StackName=STACK, ChangeSetName=name, IncludePropertyValues=True)
        if evidence_dir:
            (evidence_dir / "change-set.json").write_text(json.dumps(change, default=str, indent=2), encoding="utf-8")
        validate_change_set(change, code)
    except Exception:
        cfn.delete_change_set(StackName=STACK, ChangeSetName=name)
        raise
    cfn.execute_change_set(StackName=STACK, ChangeSetName=name)
    cfn.get_waiter("stack_update_complete").wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 90})


def parameters(stack):
    return {row["ParameterKey"]: row["ParameterValue"] for row in stack["Parameters"]}


def checked_artifact(directory, release_sha):
    evidence = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    content = (directory / "receita-shadow.zip").read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    upstream = json.loads((Path(__file__).resolve().parents[1] / "receita-worker/upstream.json").read_text(encoding="utf-8"))
    if (evidence.get("application_commit") != release_sha or evidence.get("zip_sha256") != sha
            or any(evidence.get(k) != v for k, v in upstream.items())
            or any(evidence.get(k) != "passed" for k in ("upstream_tests", "adapter_tests", "packaged_smoke"))):
        raise ValueError("Artifact commit, source, hash or tests do not match this release")
    return content, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True)
    args = parser.parse_args()
    account, release_sha = os.environ.get("AWS_ACCOUNT_ID", ""), os.environ.get("GITHUB_SHA", "")
    if (os.environ.get("GITHUB_REF") != "refs/heads/main" or not re.fullmatch(r"[0-9]{12}", account)
            or not re.fullmatch(r"[0-9a-f]{40}", release_sha)):
        raise ValueError("Release requires a valid AWS account and exact main commit")
    directory = Path(args.artifacts)
    content, sha = checked_artifact(directory, release_sha)
    bucket = f"gettdone-classification-artifacts-{account}-{REGION}"
    cfn, s3, functions = (boto3.client(service, region_name=REGION) for service in ("cloudformation", "s3", "lambda"))
    # Infrastructure is prepared once, just like the existing conversion worker.
    # This release principal deliberately cannot create stacks or runtime roles.
    before = cfn.describe_stacks(StackName=STACK)["Stacks"][0]
    previous = parameters(before)
    outputs = {row["OutputKey"]: row["OutputValue"] for row in before.get("Outputs", [])}
    if (before["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}
            or previous.get("ArtifactBucket") != bucket or outputs.get("FunctionName") != FUNCTION):
        raise ValueError("Initialize/repair the dedicated fiscal stack before publishing code")
    if s3.get_bucket_versioning(Bucket=bucket, ExpectedBucketOwner=account).get("Status") != "Enabled":
        raise ValueError("Fiscal artifact bucket must have versioning enabled")
    current = functions.get_function_configuration(FunctionName=FUNCTION)
    expected_hash = base64.b64encode(hashlib.sha256(content).digest()).decode()
    old_code = {"S3Bucket": bucket, "S3Key": previous["ArtifactKey"], "S3ObjectVersion": previous["ArtifactVersion"]}
    report = {"application_commit": release_sha, "stack": STACK, "previous_code": old_code,
              "queue_preserved": previous.get("EnableQueue"), "zip_sha256": sha}
    # Save rollback coordinates even when the AWS update or verification fails.
    (directory / "deployment-before.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if (current.get("CodeSha256") == expected_hash and current.get("LastUpdateStatus") == "Successful"
            and current.get("State") == "Active"):
        report.update({"status": "already_current", "code": old_code})
    else:
        key = f"classification-shadow/releases/{sha}.zip"
        uploaded = s3.put_object(Bucket=bucket, Key=key, Body=content, ServerSideEncryption="AES256",
                                 ExpectedBucketOwner=account, Metadata={"application-commit": release_sha})
        version = uploaded.get("VersionId")
        if not version or version == "null":
            raise ValueError("S3 did not return an immutable artifact version")
        code = {"S3Bucket": bucket, "S3Key": key, "S3ObjectVersion": version}
        name = f"classification-shadow-{release_sha[:12]}-{uuid4().hex[:12]}"
        cfn.create_change_set(StackName=STACK, ChangeSetName=name, ChangeSetType="UPDATE", UsePreviousTemplate=True,
                              Parameters=release_parameters(previous, code), Capabilities=["CAPABILITY_NAMED_IAM"],
                              RoleARN=f"arn:aws:iam::{account}:role/{EXECUTION_ROLE}",
                              Description=f"Fiscal Lambda ZIP for application commit {release_sha}")
        review_and_execute(cfn, name, code, directory)
        after = cfn.describe_stacks(StackName=STACK)["Stacks"][0]
        expected = {**previous, "ArtifactKey": key, "ArtifactVersion": version}
        deployed = functions.get_function_configuration(FunctionName=FUNCTION)
        if (after["StackStatus"] != "UPDATE_COMPLETE" or parameters(after) != expected
                or deployed.get("CodeSha256") != expected_hash or deployed.get("State") != "Active"
                or deployed.get("LastUpdateStatus") != "Successful"):
            raise ValueError("Post-deploy verification failed; inspect stack events and saved rollback coordinates")
        report.update({"status": "deployed", "code": code})
    (directory / "deployment.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("## Fiscal worker release\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")


if __name__ == "__main__":
    main()

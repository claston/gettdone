"""Deploy only the isolated shadow stack from a verified CI artifact."""
import argparse
import hashlib
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--producer-role", required=True)
    parser.add_argument("--enable-queue", choices=["true", "false"], default="false")
    args = parser.parse_args()
    directory = Path(args.artifacts)
    matches = list(directory.rglob("evidence.json"))
    if len(matches) != 1:
        raise SystemExit("Expected one build evidence file")
    evidence = json.loads(matches[0].read_text())
    archive = matches[0].parent / "receita-shadow.zip"
    content = archive.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    if sha != evidence["zip_sha256"] or any(evidence.get(k) != "passed" for k in ("upstream_tests", "adapter_tests", "packaged_smoke")):
        raise SystemExit("Artifact integrity or tests failed")
    s3, cfn = boto3.client("s3"), boto3.client("cloudformation")
    if s3.get_bucket_versioning(Bucket=args.bucket).get("Status") != "Enabled":
        raise SystemExit("Artifact bucket must have versioning enabled")
    key = "classification-shadow/releases/" + sha + ".zip"
    uploaded = s3.put_object(Bucket=args.bucket, Key=key, Body=content, ServerSideEncryption="AES256")
    parameters = {"ArtifactBucket": args.bucket, "ArtifactKey": key, "ArtifactVersion": uploaded["VersionId"],
                  "ProducerRoleName": args.producer_role, "EnableQueue": args.enable_queue}
    stack = "gettdone-classification-shadow"
    request = {"StackName": stack, "TemplateBody": Path("infra/classification-shadow.yaml").read_text(encoding="utf-8"),
               "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()], "Capabilities": ["CAPABILITY_IAM"]}
    try:
        cfn.describe_stacks(StackName=stack)
        exists = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ValidationError" or "does not exist" not in str(exc):
            raise
        exists = False
    try:
        (cfn.update_stack if exists else cfn.create_stack)(**request)
    except ClientError as exc:
        if "No updates are to be performed" not in str(exc):
            raise
    else:
        try:
            cfn.get_waiter("stack_update_complete" if exists else "stack_create_complete").wait(StackName=stack)
        except WaiterError:
            raise SystemExit("Shadow deployment did not complete; inspect stack events") from None
    print(json.dumps(cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", []), indent=2))


if __name__ == "__main__":
    main()

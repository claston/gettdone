"""Print the additive IAM policy for the existing GitHub worker deployment role."""
import argparse
import json
import re


def policy(account):
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("AWS account must contain exactly 12 digits")
    region = "us-east-1"
    stack = f"arn:aws:cloudformation:{region}:{account}:stack/gettdone-classification-shadow/*"
    changes = f"arn:aws:cloudformation:{region}:{account}:changeSet/classification-shadow-*/*"
    execution = f"arn:aws:iam::{account}:role/gettdone/GettDoneClassificationCloudFormationExecutionRole"
    bucket = f"arn:aws:s3:::gettdone-classification-artifacts-{account}-{region}"
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "ReadFiscalStack", "Effect": "Allow",
         "Action": ["cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents", "cloudformation:GetTemplate"],
         "Resource": stack},
        {"Sid": "PrepareFiscalCodeChange", "Effect": "Allow", "Action": "cloudformation:CreateChangeSet",
         "Resource": [stack, changes], "Condition": {"StringEquals": {"cloudformation:RoleArn": execution}}},
        {"Sid": "ReviewAndExecuteFiscalCodeChange", "Effect": "Allow",
         "Action": ["cloudformation:DescribeChangeSet", "cloudformation:ExecuteChangeSet", "cloudformation:DeleteChangeSet"],
         "Resource": [stack, changes]},
        {"Sid": "PassOnlyFiscalReleaseRole", "Effect": "Allow", "Action": "iam:PassRole", "Resource": execution,
         "Condition": {"StringEquals": {"iam:PassedToService": "cloudformation.amazonaws.com"}}},
        {"Sid": "CheckArtifactVersioning", "Effect": "Allow", "Action": "s3:GetBucketVersioning", "Resource": bucket},
        {"Sid": "UploadFiscalZip", "Effect": "Allow", "Action": "s3:PutObject",
         "Resource": bucket + "/classification-shadow/releases/*",
         "Condition": {"StringEquals": {"s3:x-amz-server-side-encryption": "AES256"}}},
        {"Sid": "VerifyFiscalLambda", "Effect": "Allow", "Action": "lambda:GetFunctionConfiguration",
         "Resource": f"arn:aws:lambda:{region}:{account}:function:gettdone-classification-shadow-worker"},
    ]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    args = parser.parse_args()
    print(json.dumps(policy(args.account), indent=2))

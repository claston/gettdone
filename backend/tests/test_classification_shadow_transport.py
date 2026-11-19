import boto3
import pytest

from app.adapters.conversion.classification_shadow import ShadowTransport


@pytest.mark.parametrize("region,default_region,expected", [
    ("us-east-1", None, "us-east-1"),
    (None, "eu-west-1", "eu-west-1"),
    ("us-east-1", "eu-west-1", "us-east-1"),
    (None, None, "sa-east-1"),
])
def test_transport_resolves_region_without_requiring_an_aws_profile(tmp_path, monkeypatch, region, default_region, expected):
    config = tmp_path / "config"
    config.write_text("[default]\nregion = sa-east-1\n" if region is None and default_region is None else "", encoding="utf-8")
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "AWS_CONFIG_FILE": str(config), "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "missing-credentials"),
        "AWS_ACCESS_KEY_ID": "synthetic", "AWS_SECRET_ACCESS_KEY": "synthetic", "AWS_EC2_METADATA_DISABLED": "true",
        "CLASSIFICATION_SHADOW_BUCKET": "synthetic-bucket",
        "CLASSIFICATION_SHADOW_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    if region:
        monkeypatch.setenv("AWS_REGION", region)
    if default_region:
        monkeypatch.setenv("AWS_DEFAULT_REGION", default_region)
    transport = ShadowTransport()
    try:
        assert transport.s3.meta.region_name == expected
        assert transport.sqs.meta.region_name == expected
    finally:
        transport.s3.close()
        transport.sqs.close()

import pytest

from app.security_baseline import (
    parse_cors_allow_origins,
    read_bool_env,
    validate_production_security_baseline,
)


def test_validate_baseline_is_noop_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ACCESS_CONTROL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    monkeypatch.delenv("UNLIMITED_ANON_QUOTA", raising=False)

    validate_production_security_baseline()


def test_validate_baseline_rejects_insecure_production_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ACCESS_CONTROL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.setenv("ENABLE_API_DOCS", "true")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "true")
    monkeypatch.setenv("ANONYMOUS_IDENTITY_COOKIE_SECURE", "false")

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    message = str(exc.value)
    assert "ACCESS_CONTROL_TOKEN_SECRET must be configured in production." in message
    assert "CORS_ALLOW_ORIGINS must be configured in production." in message
    assert "ENABLE_API_DOCS must be false in production." in message
    assert "UNLIMITED_ANON_QUOTA must be false in production." in message
    assert "ANONYMOUS_IDENTITY_COOKIE_SECURE must be true in production." in message


def test_validate_baseline_accepts_secure_production_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com,https://app.ofxsimples.com")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")

    validate_production_security_baseline()


def test_validate_baseline_requires_bucket_when_canonical_capture_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CANONICAL_LAYOUT_CAPTURE_ENABLED", "true")
    monkeypatch.delenv("CONVERSION_S3_BUCKET", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    assert "CONVERSION_S3_BUCKET must be configured for canonical layout capture" in str(exc.value)


def test_validate_baseline_rejects_incomplete_async_aws_cutover(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONVERSION_ARCHITECTURE_MODE", "async_aws")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CONVERSION_S3_BUCKET", raising=False)
    monkeypatch.delenv("CONVERSION_SQS_QUEUE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    message = str(exc.value)
    assert "DATABASE_URL must be PostgreSQL" in message
    assert "CONVERSION_DOCUMENT_STORE must be 's3'" in message
    assert "ANALYSIS_STORAGE must be 's3'" in message
    assert "CONVERSION_S3_BUCKET must be configured" in message
    assert "CONVERSION_SQS_QUEUE_URL must be configured" in message


def test_validate_baseline_accepts_inline_shared_fallback_without_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONVERSION_ARCHITECTURE_MODE", "async_aws")
    monkeypatch.setenv("CONVERSION_UPLOAD_MODE", "proxy")
    monkeypatch.setenv("CONVERSION_EXECUTION_MODE", "inline_shared")
    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/gettdone")
    monkeypatch.setenv("CONVERSION_DOCUMENT_STORE", "s3")
    monkeypatch.setenv("ANALYSIS_STORAGE", "s3")
    monkeypatch.setenv("CONVERSION_BATCH_REPOSITORY", "postgres")
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "private-conversions")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("CONVERSION_SQS_QUEUE_URL", raising=False)

    validate_production_security_baseline()


def test_validate_baseline_rejects_incomplete_user_scoped_async_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONVERSION_ARCHITECTURE_MODE", "legacy")
    monkeypatch.setenv("CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST", "a@a.com.br")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CONVERSION_S3_BUCKET", raising=False)
    monkeypatch.delenv("CONVERSION_SQS_QUEUE_URL", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    message = str(exc.value)
    assert "DATABASE_URL must be PostgreSQL for user-scoped async conversions" in message
    assert "CONVERSION_S3_BUCKET must be configured for user-scoped async conversions" in message
    assert "CONVERSION_SQS_QUEUE_URL must be configured for user-scoped async conversions" in message
    assert "AWS_REGION must be configured for user-scoped async conversions" in message


def test_validate_baseline_accepts_user_scoped_async_rollout_while_legacy_stays_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONVERSION_ARCHITECTURE_MODE", "legacy")
    monkeypatch.setenv("CONVERSION_UPLOAD_MODE", "proxy")
    monkeypatch.setenv("CONVERSION_EXECUTION_MODE", "inline_legacy")
    monkeypatch.setenv("CONVERSION_DOCUMENT_STORE", "filesystem")
    monkeypatch.setenv("ANALYSIS_STORAGE", "filesystem")
    monkeypatch.setenv("CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST", "a@a.com.br")
    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/gettdone?sslmode=require")
    monkeypatch.setenv("CONVERSION_BATCH_REPOSITORY", "postgres")
    monkeypatch.setenv("CONVERSION_S3_BUCKET", "private-conversions")
    monkeypatch.setenv("CONVERSION_SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/jobs")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    validate_production_security_baseline()


def test_validate_baseline_rejects_invalid_async_rollout_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONVERSION_ASYNC_USER_EMAIL_ALLOWLIST", "not-an-email")

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    assert "Invalid async conversion rollout configuration" in str(exc.value)


def test_read_bool_env_falls_back_to_default_for_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECURITY_FLAG", "not-a-bool")
    assert read_bool_env("SECURITY_FLAG", default=False) is False
    assert read_bool_env("SECURITY_FLAG", default=True) is True


def test_parse_cors_allow_origins_ignores_empty_entries() -> None:
    origins = parse_cors_allow_origins(" https://a.com, ,https://b.com ,,")
    assert origins == ["https://a.com", "https://b.com"]


def test_validate_baseline_rejects_wildcard_cors_origin_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    assert "CORS_ALLOW_ORIGINS cannot include wildcard '*'" in str(exc.value)


def test_validate_baseline_rejects_non_http_cors_origin_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "ftp://bad-origin.example")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    assert "must use http:// or https://" in str(exc.value)


def test_validate_baseline_requires_real_email_provider_when_verification_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://www.ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("AUTH_EMAIL_VERIFICATION_REQUIRED", "true")
    monkeypatch.setenv("CONTACT_RESEND_DRY_RUN", "true")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("AUTH_EMAIL_VERIFICATION_FRONTEND_URL", "http://localhost/verificar-email.html")

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    message = str(exc.value)
    assert "RESEND_API_KEY must be configured" in message
    assert "CONTACT_RESEND_DRY_RUN must be false" in message
    assert "AUTH_EMAIL_VERIFICATION_FRONTEND_URL must use HTTPS" in message


def test_validate_baseline_requires_smtp_credentials_for_hostinger_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://www.ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONTACT_DELIVERY_PROVIDER", "hostinger_smtp")
    monkeypatch.setenv("CONTACT_SMTP_DRY_RUN", "true")
    monkeypatch.delenv("CONTACT_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("CONTACT_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("CONTACT_TO_EMAIL", raising=False)

    with pytest.raises(RuntimeError) as exc:
        validate_production_security_baseline()

    message = str(exc.value)
    assert "CONTACT_SMTP_USERNAME must be configured" in message
    assert "CONTACT_SMTP_PASSWORD must be configured" in message
    assert "CONTACT_TO_EMAIL must be configured" in message
    assert "CONTACT_SMTP_DRY_RUN must be false" in message


def test_validate_baseline_accepts_hostinger_smtp_contact_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_CONTROL_TOKEN_SECRET", "a" * 40)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://www.ofxsimples.com.br")
    monkeypatch.setenv("ENABLE_API_DOCS", "false")
    monkeypatch.setenv("UNLIMITED_ANON_QUOTA", "false")
    monkeypatch.setenv("CONTACT_DELIVERY_PROVIDER", "hostinger_smtp")
    monkeypatch.setenv("CONTACT_SMTP_DRY_RUN", "false")
    monkeypatch.setenv("CONTACT_SMTP_USERNAME", "contato@ofxsimples.com.br")
    monkeypatch.setenv("CONTACT_SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setenv("CONTACT_TO_EMAIL", "contato@ofxsimples.com.br")

    validate_production_security_baseline()

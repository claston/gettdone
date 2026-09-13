import os

_PRODUCTION_ENV_NAMES = {"production", "prod"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_LOCALHOST_MARKERS = ("localhost", "127.0.0.1")
_INSECURE_DEFAULT_TOKEN_SECRET = "dev-access-control-secret"


def get_app_env() -> str:
    raw = os.getenv("APP_ENV", "development").strip().lower()
    return raw or "development"


def is_production_env() -> bool:
    return get_app_env() in _PRODUCTION_ENV_NAMES


def read_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def parse_cors_allow_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def validate_production_security_baseline() -> None:
    if not is_production_env():
        return

    issues: list[str] = []

    token_secret = os.getenv("ACCESS_CONTROL_TOKEN_SECRET", "").strip()
    if not token_secret:
        issues.append("ACCESS_CONTROL_TOKEN_SECRET must be configured in production.")
    elif token_secret == _INSECURE_DEFAULT_TOKEN_SECRET:
        issues.append("ACCESS_CONTROL_TOKEN_SECRET cannot use the insecure development default in production.")
    elif len(token_secret) < 32:
        issues.append("ACCESS_CONTROL_TOKEN_SECRET must have at least 32 characters in production.")

    cors_raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if not cors_raw:
        issues.append("CORS_ALLOW_ORIGINS must be configured in production.")
    else:
        origins = parse_cors_allow_origins(cors_raw)
        if not origins:
            issues.append("CORS_ALLOW_ORIGINS must include at least one valid origin in production.")
        wildcard_origins = [origin for origin in origins if origin == "*"]
        if wildcard_origins:
            issues.append("CORS_ALLOW_ORIGINS cannot include wildcard '*' in production.")
        invalid_scheme_origins = [
            origin
            for origin in origins
            if not (origin.lower().startswith("http://") or origin.lower().startswith("https://"))
        ]
        if invalid_scheme_origins:
            issues.append("CORS_ALLOW_ORIGINS must use http:// or https:// origins in production.")
        localhost_origins = [
            origin
            for origin in origins
            if any(marker in origin.lower() for marker in _LOCALHOST_MARKERS)
        ]
        if localhost_origins:
            issues.append("CORS_ALLOW_ORIGINS cannot include localhost/127.0.0.1 in production.")

    if read_bool_env("ENABLE_API_DOCS", default=False):
        issues.append("ENABLE_API_DOCS must be false in production.")

    if read_bool_env("UNLIMITED_ANON_QUOTA", default=False):
        issues.append("UNLIMITED_ANON_QUOTA must be false in production.")

    if not read_bool_env("ANONYMOUS_IDENTITY_COOKIE_SECURE", default=True):
        issues.append("ANONYMOUS_IDENTITY_COOKIE_SECURE must be true in production.")

    if read_bool_env("CANONICAL_LAYOUT_CAPTURE_ENABLED", default=False):
        if not os.getenv("CONVERSION_S3_BUCKET", "").strip():
            issues.append("CONVERSION_S3_BUCKET must be configured for canonical layout capture.")

    contact_provider = os.getenv("CONTACT_DELIVERY_PROVIDER", "resend").strip().lower()
    if contact_provider not in {"resend", "smtp", "hostinger_smtp"}:
        issues.append("CONTACT_DELIVERY_PROVIDER must be 'resend' or 'hostinger_smtp'.")
    elif contact_provider in {"smtp", "hostinger_smtp"}:
        for variable_name in ("CONTACT_SMTP_USERNAME", "CONTACT_SMTP_PASSWORD", "CONTACT_TO_EMAIL"):
            if not os.getenv(variable_name, "").strip():
                issues.append(f"{variable_name} must be configured for Hostinger SMTP contact delivery.")
        if read_bool_env("CONTACT_SMTP_DRY_RUN", default=True):
            issues.append("CONTACT_SMTP_DRY_RUN must be false for Hostinger SMTP contact delivery.")

    if read_bool_env("AUTH_EMAIL_VERIFICATION_REQUIRED", default=False):
        if not os.getenv("RESEND_API_KEY", "").strip():
            issues.append("RESEND_API_KEY must be configured when email verification is enabled in production.")
        if read_bool_env("CONTACT_RESEND_DRY_RUN", default=True):
            issues.append("CONTACT_RESEND_DRY_RUN must be false when email verification is enabled in production.")
        verification_url = os.getenv("AUTH_EMAIL_VERIFICATION_FRONTEND_URL", "").strip()
        if not verification_url.lower().startswith("https://"):
            issues.append("AUTH_EMAIL_VERIFICATION_FRONTEND_URL must use HTTPS in production.")

    async_rollout_enabled = False
    try:
        from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy

        async_rollout_enabled = AsyncConversionRolloutPolicy.from_mapping(os.environ).enabled
    except ValueError as exc:
        issues.append(f"Invalid async conversion rollout configuration: {exc}")

    conversion_execution_mode = os.getenv("CONVERSION_EXECUTION_MODE", "").strip().lower()
    try:
        from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig

        conversion_runtime = ConversionRuntimeConfig.from_mapping(os.environ)
        conversion_execution_mode = conversion_runtime.execution_mode.value
    except ValueError as exc:
        issues.append(f"Invalid conversion runtime configuration: {exc}")

    async_architecture_enabled = (
        os.getenv("CONVERSION_ARCHITECTURE_MODE", "legacy").strip().lower() == "async_aws"
    )
    if async_architecture_enabled:
        database_url = os.getenv("DATABASE_URL", "").strip().lower()
        if not (database_url.startswith("postgres://") or database_url.startswith("postgresql://")):
            issues.append("DATABASE_URL must be PostgreSQL for async_aws conversions.")
        if os.getenv("CONVERSION_DOCUMENT_STORE", "filesystem").strip().lower() != "s3":
            issues.append("CONVERSION_DOCUMENT_STORE must be 's3' for async_aws conversions.")
        if os.getenv("ANALYSIS_STORAGE", "filesystem").strip().lower() != "s3":
            issues.append("ANALYSIS_STORAGE must be 's3' for async_aws conversions.")
        if os.getenv("CONVERSION_BATCH_REPOSITORY", "postgres").strip().lower() != "postgres":
            issues.append("CONVERSION_BATCH_REPOSITORY must be 'postgres' for async_aws conversions.")
        if not os.getenv("CONVERSION_S3_BUCKET", "").strip():
            issues.append("CONVERSION_S3_BUCKET must be configured for async_aws conversions.")
        if conversion_execution_mode == "sqs_lambda":
            queue_url = os.getenv("CONVERSION_SQS_QUEUE_URL", "").strip()
            if not queue_url:
                issues.append("CONVERSION_SQS_QUEUE_URL must be configured for async_aws conversions.")
            elif not queue_url.startswith("https://sqs."):
                issues.append("CONVERSION_SQS_QUEUE_URL must be an HTTPS AWS SQS URL.")
        if not (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")):
            issues.append("AWS_REGION must be configured for async_aws conversions.")
    elif async_rollout_enabled:
        database_url = os.getenv("DATABASE_URL", "").strip().lower()
        if not (database_url.startswith("postgres://") or database_url.startswith("postgresql://")):
            issues.append("DATABASE_URL must be PostgreSQL for user-scoped async conversions.")
        if os.getenv("CONVERSION_BATCH_REPOSITORY", "postgres").strip().lower() != "postgres":
            issues.append("CONVERSION_BATCH_REPOSITORY must be 'postgres' for user-scoped async conversions.")
        if not os.getenv("CONVERSION_S3_BUCKET", "").strip():
            issues.append("CONVERSION_S3_BUCKET must be configured for user-scoped async conversions.")
        queue_url = os.getenv("CONVERSION_SQS_QUEUE_URL", "").strip()
        if not queue_url:
            issues.append("CONVERSION_SQS_QUEUE_URL must be configured for user-scoped async conversions.")
        elif not queue_url.startswith("https://sqs."):
            issues.append("CONVERSION_SQS_QUEUE_URL must be an HTTPS AWS SQS URL.")
        if not (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")):
            issues.append("AWS_REGION must be configured for user-scoped async conversions.")

    if issues:
        details = "\n- ".join(issues)
        raise RuntimeError(f"Production security baseline validation failed:\n- {details}")


__all__ = [
    "get_app_env",
    "is_production_env",
    "read_bool_env",
    "parse_cors_allow_origins",
    "validate_production_security_baseline",
]

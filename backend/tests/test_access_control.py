import sqlite3
from datetime import datetime, timedelta, timezone

from app.application.access_control import (
    ANONYMOUS_QUOTA_LIMIT,
    REGISTERED_QUOTA_LIMIT,
    AccessControlService,
)
from app.application.errors import (
    EmailVerificationRateLimitedError,
    EmailVerificationRequiredError,
    FileTooLargeError,
    GoogleOAuthAccountNotFoundError,
    GoogleOAuthExchangeError,
    InvalidEmailVerificationTokenError,
    InvalidUserTokenError,
    QuotaExceededError,
)


def test_local_user_requires_email_verification_when_enabled(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        email_verification_required=True,
    )

    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")

    assert registered.email_verification_status == "pending"
    assert registered.email_verified_at is None
    try:
        service.authenticate_user(email="erica@example.com", password="strong-pass")
        assert False, "Expected EmailVerificationRequiredError"
    except EmailVerificationRequiredError:
        assert True
    try:
        service.resolve_identity(anonymous_fingerprint=None, user_token=registered.token)
        assert False, "Expected InvalidUserTokenError"
    except InvalidUserTokenError:
        assert True


def test_email_verification_token_is_hashed_single_use_and_verifies_user(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        email_verification_required=True,
    )
    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")

    issued = service.issue_email_verification_token(user_id=registered.user_id)

    with service._connect() as conn:
        row = service._fetchone(
            conn,
            "SELECT token_hash, delivery_status FROM email_verification_tokens WHERE id = ?",
            (issued.token_id,),
        )
    assert row is not None
    assert row["token_hash"] != issued.token
    assert issued.token not in str(row["token_hash"])
    assert row["delivery_status"] == "pending"

    verified = service.confirm_email_verification_token(token=issued.token)
    assert verified.email_verification_status == "verified"
    assert verified.email_verified_at
    assert service.authenticate_user(email="erica@example.com", password="strong-pass").user_id == registered.user_id

    try:
        service.confirm_email_verification_token(token=issued.token)
        assert False, "Expected InvalidEmailVerificationTokenError"
    except InvalidEmailVerificationTokenError:
        assert True


def test_email_verification_resend_enforces_cooldown(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        email_verification_required=True,
        email_verification_resend_cooldown_seconds=60,
    )
    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    first = service.issue_email_verification_token(user_id=registered.user_id)
    service.mark_email_verification_delivery(token_id=first.token_id, status="sent", provider_message_id="msg_1")

    try:
        service.issue_email_verification_token(user_id=registered.user_id, enforce_resend_limits=True)
        assert False, "Expected EmailVerificationRateLimitedError"
    except EmailVerificationRateLimitedError:
        assert True


def test_email_verification_resend_enforces_daily_limit_and_invalidates_previous_tokens(tmp_path) -> None:
    now_box = [datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)]
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        email_verification_required=True,
        email_verification_resend_cooldown_seconds=60,
        email_verification_daily_limit=5,
        now_provider=lambda: now_box[0],
    )
    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    tokens = []
    for index in range(5):
        issued = service.issue_email_verification_token(
            user_id=registered.user_id,
            enforce_resend_limits=index > 0,
        )
        tokens.append(issued)
        service.mark_email_verification_delivery(
            token_id=issued.token_id,
            status="sent",
            provider_message_id=f"msg_{index}",
        )
        now_box[0] = now_box[0] + timedelta(seconds=61)

    try:
        service.confirm_email_verification_token(token=tokens[0].token)
        assert False, "Expected InvalidEmailVerificationTokenError for invalidated token"
    except InvalidEmailVerificationTokenError:
        assert True

    try:
        service.issue_email_verification_token(
            user_id=registered.user_id,
            enforce_resend_limits=True,
        )
        assert False, "Expected EmailVerificationRateLimitedError"
    except EmailVerificationRateLimitedError:
        assert True


def test_google_login_verifies_pending_local_account(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        email_verification_required=True,
    )
    pending = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")

    google_user = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-verified",
        email="erica@example.com",
        name="Erica",
    )

    assert google_user.user_id == pending.user_id
    assert google_user.email_verification_status == "verified"
    assert google_user.email_verified_at


def test_anonymous_quota_blocks_4th_attempt(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    identity = service.resolve_identity(anonymous_fingerprint="anon-device-a", user_token=None)
    assert identity.identity_type == "anonymous"
    assert identity.quota_limit == ANONYMOUS_QUOTA_LIMIT
    assert identity.max_pages_per_file == 10

    assert service.get_remaining_quota(identity) == 3
    assert service.consume_quota(identity) == 2
    assert service.consume_quota(identity) == 1
    assert service.consume_quota(identity) == 0
    assert service.get_remaining_quota(identity) == 0

    try:
        service.consume_quota(identity)
        assert False, "Expected QuotaExceededError"
    except QuotaExceededError:
        assert True


def test_conversion_quota_consumption_is_idempotent_per_job(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    identity = service.resolve_identity(anonymous_fingerprint="anon-device-idempotent", user_token=None)

    first = service.consume_quota(identity, consumed_units=1, idempotency_key="job_123")
    replay = service.consume_quota(identity, consumed_units=1, idempotency_key="job_123")

    assert first == 2
    assert replay == 2
    assert service.get_remaining_quota(identity) == 2


def test_register_user_gets_10_quota_and_valid_token(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=registered.token)

    assert identity.identity_type == "user"
    assert identity.quota_limit == REGISTERED_QUOTA_LIMIT
    assert identity.max_pages_per_file == 10
    assert service.get_remaining_quota(identity) == 10


def test_inactive_user_cannot_resolve_conversion_identity(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")

    service.set_user_active_status(user_id=registered.user_id, is_active=False)

    try:
        service.resolve_identity(anonymous_fingerprint=None, user_token=registered.token)
        assert False, "Expected InvalidUserTokenError"
    except InvalidUserTokenError:
        assert True


def test_inactive_user_cannot_authenticate_with_google_identity(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    registered = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-123",
        email="erica@example.com",
        name="Erica",
    )
    service.set_user_active_status(user_id=registered.user_id, is_active=False)

    try:
        service.register_or_authenticate_google_user(
            provider_user_id="google-sub-123",
            email="erica@example.com",
            name="Erica",
        )
        assert False, "Expected GoogleOAuthExchangeError"
    except GoogleOAuthExchangeError:
        assert True


def test_sqlite_legacy_database_adds_user_statuses_and_keeps_existing_user_active(tmp_path) -> None:
    state_file = tmp_path / "legacy-state.json"
    db_file = state_file.with_suffix(".db")
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                is_admin INTEGER NOT NULL DEFAULT 0,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                auth_provider TEXT NOT NULL DEFAULT 'local',
                provider_user_id TEXT,
                terms_accepted_at TEXT,
                privacy_accepted_at TEXT,
                product_updates_opt_in INTEGER NOT NULL DEFAULT 0,
                product_updates_opted_in_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO users (
                id, name, email, is_admin, password_hash, password_salt,
                auth_provider, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "usr_legacy123456",
                "Legacy User",
                "legacy@example.com",
                0,
                "hash",
                "salt",
                "local",
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        conn.commit()

    service = AccessControlService(state_file=state_file, token_secret="test-secret")

    with service._connect() as conn:
        row = service._fetchone(
            conn,
            """
            SELECT is_active, email_verification_status, email_verified_at
            FROM users WHERE id = ?
            """,
            ("usr_legacy123456",),
        )

    assert row is not None
    assert bool(row["is_active"]) is True
    assert row["email_verification_status"] == "verified"
    assert row["email_verified_at"] == "2026-08-01T00:00:00+00:00"
    with service._connect() as conn:
        token_table = service._fetchone(
            conn,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'email_verification_tokens'",
        )
    assert token_table is not None


def test_expired_email_verification_token_is_rejected(tmp_path) -> None:
    now_box = [datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)]
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        email_verification_required=True,
        email_verification_ttl_seconds=300,
        now_provider=lambda: now_box[0],
    )
    registered = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    issued = service.issue_email_verification_token(user_id=registered.user_id)
    now_box[0] = now_box[0] + timedelta(minutes=6)

    try:
        service.confirm_email_verification_token(token=issued.token)
        assert False, "Expected InvalidEmailVerificationTokenError"
    except InvalidEmailVerificationTokenError:
        assert True


def test_upload_larger_than_5mb_is_rejected(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    raw = b"a" * ((5 * 1024 * 1024) + 1)
    try:
        service.assert_upload_size(raw)
        assert False, "Expected FileTooLargeError"
    except FileTooLargeError:
        assert True


def test_invalid_token_is_rejected(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    try:
        service.resolve_identity(anonymous_fingerprint=None, user_token="invalid.token")
        assert False, "Expected InvalidUserTokenError"
    except InvalidUserTokenError:
        assert True


def test_custom_anonymous_quota_limit_is_applied(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        anonymous_quota_limit=99,
    )

    identity = service.resolve_identity(anonymous_fingerprint="anon-device-b", user_token=None)
    assert identity.identity_type == "anonymous"
    assert identity.quota_limit == 99
    assert service.get_remaining_quota(identity) == 99


def test_anonymous_quota_resets_after_week_window(tmp_path) -> None:
    now_box = [datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)]
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        now_provider=lambda: now_box[0],
    )
    identity = service.resolve_identity(anonymous_fingerprint="anon-device-weekly", user_token=None)

    assert service.consume_quota(identity) == 2
    assert service.consume_quota(identity) == 1
    assert service.consume_quota(identity) == 0
    try:
        service.consume_quota(identity)
        assert False, "Expected QuotaExceededError"
    except QuotaExceededError:
        assert True

    now_box[0] = now_box[0] + timedelta(days=8)
    assert service.get_remaining_quota(identity) == 3
    assert service.consume_quota(identity) == 2


def test_google_user_is_created_and_reused_by_provider_id(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    first = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-1",
        email="erica@example.com",
        name="Erica",
    )
    second = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-1",
        email="erica@example.com",
        name="Erica Souza",
    )

    assert first.user_id == second.user_id
    assert second.name == "Erica Souza"
    assert second.token


def test_google_signup_persists_terms_and_opt_in(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    created = service.register_or_authenticate_google_user(
        provider_user_id="google-sub-consent",
        email="erica@example.com",
        name="Erica",
        allow_create=True,
        terms_accepted_at="2026-06-10T12:00:00+00:00",
        privacy_accepted_at="2026-06-10T12:00:00+00:00",
        product_updates_opt_in=True,
        product_updates_opted_in_at="2026-06-10T12:00:00+00:00",
    )

    with service._connect() as conn:
        row = service._fetchone(
            conn,
            """
            SELECT terms_accepted_at, privacy_accepted_at, product_updates_opt_in, product_updates_opted_in_at
            FROM users
            WHERE id = ?
            """,
            (created.user_id,),
        )

    assert row is not None
    assert row["terms_accepted_at"] == "2026-06-10T12:00:00+00:00"
    assert row["privacy_accepted_at"] == "2026-06-10T12:00:00+00:00"
    assert bool(row["product_updates_opt_in"]) is True
    assert row["product_updates_opted_in_at"] == "2026-06-10T12:00:00+00:00"


def test_google_login_does_not_create_missing_account(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    try:
        service.register_or_authenticate_google_user(
            provider_user_id="google-sub-missing",
            email="newuser@example.com",
            name="New User",
            allow_create=False,
        )
        assert False, "Expected GoogleOAuthAccountNotFoundError"
    except GoogleOAuthAccountNotFoundError:
        assert True


def test_google_oauth_state_can_be_consumed_once(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    state, verifier = service.create_google_oauth_state(next_path="/client-area.html", ttl_seconds=600)
    consumed = service.consume_google_oauth_state(state=state)
    consumed_again = service.consume_google_oauth_state(state=state)

    assert consumed is not None
    assert consumed["state"] == state
    assert consumed["code_verifier"] == verifier
    assert consumed["next_path"] == "/client-area.html"
    assert consumed_again is None


def test_public_plans_are_seeded_with_versions(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    plans = service.list_public_plans()
    codes = {str(item["code"]) for item in plans}
    assert {"essencial", "profissional", "escritorio"}.issubset(codes)
    assert all(int(item["version"]) >= 1 for item in plans)
    prices = {str(item["code"]): int(item["price_cents"]) for item in plans}
    assert prices["essencial"] == 2990
    assert prices["profissional"] == 3990
    assert prices["escritorio"] == 4990


def test_public_plan_seed_recovers_missing_default_rows(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    with service._connect() as conn:
        service._execute(conn, "DELETE FROM plan_versions WHERE code = ?", ("escritorio",))
        conn.commit()

    restored_service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    plans = restored_service.list_public_plans()
    codes = {str(item["code"]) for item in plans}
    assert {"essencial", "profissional", "escritorio"}.issubset(codes)


def test_registered_user_can_use_pages_plan_quota(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    service.activate_user_plan(user_id=user.user_id, plan_code="essencial")

    identity = service.resolve_identity(anonymous_fingerprint=None, user_token=user.token)
    assert identity.identity_type == "user"
    assert identity.quota_mode == "pages"
    assert identity.quota_limit == 150
    assert identity.plan_code == "essencial"
    assert identity.max_pages_per_file_ocr == 6

    service.ensure_quota_available(identity, required_units=10)
    remaining = service.consume_quota(identity, consumed_units=10)
    assert remaining == 140


def test_list_user_conversions_marks_expired_items(tmp_path) -> None:
    now_box = [datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)]
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
        now_provider=lambda: now_box[0],
    )
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    service.record_user_conversion(
        user_id=user.user_id,
        processing_id="an_old",
        filename="old.pdf",
        model="Nubank",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=3,
        pages_count=4,
        expires_at=(now_box[0] - timedelta(minutes=1)).isoformat(),
    )
    items = service.list_user_conversions(user_id=user.user_id, limit=20)
    assert len(items) == 1
    assert items[0]["processing_id"] == "an_old"
    assert items[0]["status"] == "Expirado"
    assert items[0]["pages_count"] == 4


def test_record_user_conversion_persists_warning_metrics(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    service.record_user_conversion(
        user_id=user.user_id,
        processing_id="an_metrics_user_001",
        filename="statement.pdf",
        model="nubank_statement_ptbr",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=12,
        pages_count=5,
        canonical_warning_transactions_count=2,
        balance_consistency_failed=1,
        layout_inference_name="nubank_statement_ptbr",
        layout_inference_confidence=0.98,
        selected_parser="tabular",
        parser_confidence_band="low",
        parser_coverage_rate=0.9,
        warning_types=["balance_consistency_failed", "amount_sign_inferred"],
        quality_issues=[
            {
                "scope": "transaction",
                "severity": "error",
                "issue_code": "balance_consistency_failed",
                "transaction_index": 3,
                "source_page": 2,
                "source_line": 17,
                "source_parser": "tabular",
            }
        ],
    )

    with service._connect() as conn:
        row = service._fetchone(
            conn,
            """
            SELECT
              canonical_warning_transactions_count,
              balance_consistency_failed,
              quality_status,
              quality_rule_version,
              quality_reason_codes_json,
              parser_confidence_band,
              parser_coverage_rate,
              warning_types_json
            FROM user_conversions
            WHERE analysis_id = ?
            """,
            ("an_metrics_user_001",),
        )

    assert row is not None
    assert int(row["canonical_warning_transactions_count"]) == 2
    assert int(row["balance_consistency_failed"]) == 1
    assert str(row["quality_status"]) == "review"
    assert str(row["quality_rule_version"])
    assert "row_warnings" in str(row["quality_reason_codes_json"])
    assert str(row["parser_confidence_band"]) == "low"
    assert float(row["parser_coverage_rate"]) == 0.9
    assert "amount_sign_inferred" in str(row["warning_types_json"])

    with service._connect() as conn:
        issue = service._fetchone(
            conn,
            """
            SELECT issue_code, transaction_index, source_page, source_line, source_parser
            FROM conversion_quality_issues
            WHERE conversion_id = ? AND identity_type = ?
            """,
            ("an_metrics_user_001", "registered"),
        )

    assert issue is not None
    assert str(issue["issue_code"]) == "balance_consistency_failed"
    assert int(issue["transaction_index"]) == 3
    assert int(issue["source_page"]) == 2
    assert int(issue["source_line"]) == 17
    assert str(issue["source_parser"]) == "tabular"


def test_create_checkout_intent_persists_pending_order(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    user = service.register_user(name="Erica Souza", email="erica@example.com", password="strong-pass")

    intent = service.create_checkout_intent(
        user_id=user.user_id,
        plan_code="profissional",
        customer_name="Erica Souza",
        customer_email="erica@example.com",
        customer_whatsapp="+55 11 99999-1111",
        customer_document="123.456.789-00",
        customer_notes="Contato preferencial por WhatsApp",
    )
    assert str(intent["id"]).startswith("chk_")
    assert intent["status"] == "REQUESTED"
    assert intent["plan_code"] == "profissional"
    assert intent["price_cents"] == 3990


def test_record_anonymous_conversion_event_persists_metrics(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    service.record_anonymous_conversion_event(
        event_id="anon_evt_test_001",
        anonymous_fingerprint="fp-anon-1",
        filename="statement.pdf",
        model="nubank_statement_ptbr",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=12,
        pages_count=5,
        scanned_likely=True,
        ocr_used=True,
        ocr_pages_processed=5,
        duration_ms=1842,
        canonical_warning_transactions_count=2,
        balance_consistency_failed=1,
        error_code=None,
    )

    with service._connect() as conn:
        row = service._fetchone(
            conn,
            """
            SELECT
              anonymous_fingerprint,
              filename,
              model,
              conversion_type,
              status,
              transactions_count,
              pages_count,
              scanned_likely,
              ocr_used,
              ocr_pages_processed,
              duration_ms,
              canonical_warning_transactions_count,
              balance_consistency_failed
            FROM anonymous_conversion_events
            WHERE id = ?
            """,
            ("anon_evt_test_001",),
        )

    assert row is not None
    assert str(row["anonymous_fingerprint"]) == "fp-anon-1"
    assert str(row["filename"]) == "statement.pdf"
    assert str(row["model"]) == "nubank_statement_ptbr"
    assert str(row["conversion_type"]) == "pdf-ofx"
    assert str(row["status"]) == "Sucesso"
    assert int(row["transactions_count"]) == 12
    assert int(row["pages_count"]) == 5
    assert int(row["ocr_pages_processed"]) == 5
    assert int(row["duration_ms"]) == 1842
    assert int(row["canonical_warning_transactions_count"]) == 2
    assert int(row["balance_consistency_failed"]) == 1


def test_retryable_db_exception_includes_unexpected_ssl_close(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )
    exc = Exception("consuming input failed: SSL connection has been closed unexpectedly")
    assert service._is_retryable_db_exception(exc) is True


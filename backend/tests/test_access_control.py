from datetime import datetime, timedelta, timezone

from app.application.access_control import (
    ANONYMOUS_QUOTA_LIMIT,
    REGISTERED_QUOTA_LIMIT,
    AccessControlService,
)
from app.application.errors import (
    FileTooLargeError,
    GoogleOAuthAccountNotFoundError,
    InvalidUserTokenError,
    QuotaExceededError,
)


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
    )

    with service._connect() as conn:
        row = service._fetchone(
            conn,
            """
            SELECT
              canonical_warning_transactions_count,
              balance_consistency_failed
            FROM user_conversions
            WHERE analysis_id = ?
            """,
            ("an_metrics_user_001",),
        )

    assert row is not None
    assert int(row["canonical_warning_transactions_count"]) == 2
    assert int(row["balance_consistency_failed"]) == 1


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


def test_refresh_postgres_pool_checks_connections_when_pool_exists(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    class FakePool:
        def __init__(self) -> None:
            self.checked = False

        def check(self) -> None:
            self.checked = True

    pool = FakePool()
    service._postgres_pool = pool

    service.db.refresh_postgres_pool()

    assert pool.checked is True


def test_refresh_postgres_pool_swallows_health_check_failures(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "state.json",
        token_secret="test-secret",
    )

    class FakePool:
        def check(self) -> None:
            raise RuntimeError("pool check failed")

    service._postgres_pool = FakePool()

    service.db.refresh_postgres_pool()


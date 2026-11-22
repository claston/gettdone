from types import SimpleNamespace

import pytest

from app.application.conversion.quota_validator_service import QuotaValidatorService


class Access:
    def __init__(self):
        self.charges = []
        self.checks = []

    def get_remaining_quota(self, identity):
        return 0

    def ensure_quota_available(self, identity, required_units):
        self.checks.append(required_units)

    def consume_quota(self, identity, consumed_units, idempotency_key=None):
        self.charges.append((consumed_units, idempotency_key))
        return 10 - consumed_units


@pytest.mark.parametrize("file_type", ["ofx", "csv", "xlsx"])
def test_structured_office_import_works_without_remaining_quota(file_type):
    access = Access()
    service = QuotaValidatorService(access_control_service=access)
    identity = SimpleNamespace(identity_type="user", batch_conversion_enabled=True, quota_mode="document_conversion")
    service.ensure_conversion_quota_available(identity=identity, file_type=file_type, product="pro")
    result = service.consume_quota_for_conversion(identity=identity, analysis=SimpleNamespace(file_type=file_type), product="pro")
    assert result.consumed_units == 0
    assert access.charges == access.checks == []


def test_office_conversion_counts_one_document_and_retains_legacy_page_billing():
    access = Access()
    service = QuotaValidatorService(access_control_service=access)
    analysis = SimpleNamespace(file_type="pdf", pdf_processing_metrics={"page_count": 8})
    office = SimpleNamespace(identity_type="user", batch_conversion_enabled=True, quota_mode="document_conversion")
    assert (
        service.consume_quota_for_conversion(identity=office, analysis=analysis, product="pro", idempotency_key="job").consumed_units == 1
    )
    legacy = SimpleNamespace(quota_mode="pages")
    assert service.consume_quota_for_conversion(identity=legacy, analysis=analysis).consumed_units == 8
    assert access.charges == [(1, "job"), (8, None)]


def test_free_ofx_is_not_changed_even_when_the_account_has_the_new_plan():
    access = Access()
    service = QuotaValidatorService(access_control_service=access)
    identity = SimpleNamespace(identity_type="user", batch_conversion_enabled=True, quota_mode="document_conversion")
    result = service.consume_quota_for_conversion(identity=identity, analysis=SimpleNamespace(file_type="ofx"))
    assert result.consumed_units == 1

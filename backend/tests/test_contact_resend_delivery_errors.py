import asyncio

import httpx
import pytest

from app.application.contact_service import ContactService
from app.application.errors import ContactDeliveryError, ContactDeliveryRejectedError


@pytest.mark.parametrize("status,body,rejected", [
    (429, {"name": "daily_quota_exceeded"}, True),
    (429, {"name": "monthly_quota_exceeded"}, True),
    (429, {"name": "rate_limit_exceeded"}, True),
    (400, {"name": "validation_error"}, True),
    (401, {"name": "missing_api_key"}, True),
    (403, {"name": "validation_error"}, True),
    (422, {"name": "missing_required_field"}, True),
    (409, {"name": "concurrent_idempotent_requests"}, False),
    (409, {"name": "invalid_idempotent_request"}, False),
    (500, {"name": "application_error"}, False),
    (503, {"name": "daily_quota_exceeded"}, False),
    (429, {"name": "unknown_error"}, False),
    (429, {"name": ["daily_quota_exceeded"]}, False),
    (429, None, False),
    (429, ["daily_quota_exceeded"], False),
    (429, "upstream error", False),
    (200, {}, False),
])
def test_resend_confirms_only_documented_refusals(monkeypatch, status, body, rejected):
    def handler(request):
        if isinstance(body, str):
            return httpx.Response(status, content=body, request=request)
        return httpx.Response(status, json=body, request=request)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    mailer = ContactService(api_key="fake", from_email="test@example.com", to_email="support@example.com", dry_run=False)
    with pytest.raises(ContactDeliveryError) as caught:
        asyncio.run(mailer.send_text_email(to_email="recipient@example.com", subject="Guide", text="Guide"))
    assert isinstance(caught.value, ContactDeliveryRejectedError) is rejected


def test_resend_transport_timeout_remains_ambiguous(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("Response unavailable", request=request)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    mailer = ContactService(api_key="fake", from_email="test@example.com", to_email="support@example.com", dry_run=False)
    with pytest.raises(ContactDeliveryError) as caught:
        asyncio.run(mailer.send_text_email(to_email="recipient@example.com", subject="Guide", text="Guide"))
    assert not isinstance(caught.value, ContactDeliveryRejectedError)

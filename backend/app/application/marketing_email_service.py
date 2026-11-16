"""Manual marketing delivery; consent and templates are shared with future sequences."""

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from app.application.access_control import AccessControlService
from app.application.contact_service import ContactDeliveryResult, ContactService

TEST_TEMPLATE_ID = "marketing-test-v1"
TEST_SUBJECT = "OFX Simples — e-mail de teste"
ORGANIZATION_TEMPLATE_ID = "01-organizacao"
CONTENT_TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / "marketing" / "templates" / "ofx-content-series"


class MarketingRecipientUnavailableError(Exception):
    pass


class InvalidMarketingUnsubscribeTokenError(Exception):
    pass


class MarketingConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class MarketingEmailTemplate:
    template_id: str
    subject: str

    def render(self, *, name: str, unsubscribe_url: str) -> str:
        return "\n".join([
            f"Olá, {name}!", "",
            "Este é um e-mail de teste do OFX Simples.",
            "Estamos preparando conteúdos e novidades para ajudar na sua rotina financeira.",
            "Você recebeu esta mensagem porque autorizou nossas comunicações por e-mail.", "",
            "Não quero receber mais e-mails:", unsubscribe_url, "",
            "O cancelamento vale para comunicações de marketing. Mensagens necessárias à sua conta continuam disponíveis.",
            "", "Equipe OFX Simples",
        ])


TEST_TEMPLATE = MarketingEmailTemplate(TEST_TEMPLATE_ID, TEST_SUBJECT)


def render_organization_email(*, name: str, unsubscribe_url: str) -> tuple[str, str, str]:
    values = {
        "name": name,
        "unsubscribe_url": unsubscribe_url,
        "sender_company": os.getenv("MARKETING_SENDER_COMPANY", "").strip(),
        "sender_cnpj": os.getenv("MARKETING_SENDER_CNPJ", "").strip(),
    }
    if any(not value or "{{" in value or "}}" in value for value in values.values()):
        raise MarketingConfigurationError("Marketing template values must not contain placeholders.")
    try:
        campaign = json.loads((CONTENT_TEMPLATE_ROOT / "campaign.json").read_text(encoding="utf-8"))
        subject = next(item["subject"] for item in campaign["emails"] if item["id"] == ORGANIZATION_TEMPLATE_ID)
        bodies = []
        for extension in ("txt", "html"):
            source = (CONTENT_TEMPLATE_ROOT / f"{ORGANIZATION_TEMPLATE_ID}.{extension}").read_text(encoding="utf-8")
            body = re.sub(
                r"\{\{\s*(\w+)\s*\}\}",
                lambda match: escape(values[match[1]], quote=True) if extension == "html" else values[match[1]],
                source,
            )
            if "{{" in body or "}}" in body:
                raise MarketingConfigurationError("Marketing template contains unresolved placeholders.")
            bodies.append(body)
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise MarketingConfigurationError("Marketing template is unavailable or invalid.") from exc
    return subject, bodies[0], bodies[1]


class MarketingEmailService:
    def __init__(self, access_control: AccessControlService):
        self.access_control = access_control
        self.test_recipient = os.getenv("MARKETING_TEST_RECIPIENT", "").strip().lower()
        self.from_email = os.getenv("MARKETING_FROM_EMAIL", "").strip() or None

    async def send_test(
        self, *, mailer: ContactService, public_base_url: str, request_id: str, template_id: str = TEST_TEMPLATE_ID,
    ) -> ContactDeliveryResult:
        if template_id not in {TEST_TEMPLATE_ID, ORGANIZATION_TEMPLATE_ID}:
            raise MarketingConfigurationError("Unsupported marketing test template.")
        if not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", self.test_recipient):
            raise MarketingConfigurationError("MARKETING_TEST_RECIPIENT must contain a single email address.")
        try:
            parsed = urlsplit(public_base_url)
            local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (
                (parsed.scheme != "https" and not local_http)
                or not parsed.hostname
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path.strip("/")
                or parsed.netloc.endswith(":")
                or parsed.port == 0
            ):
                raise ValueError
        except ValueError as exc:
            raise MarketingConfigurationError("MARKETING_PUBLIC_BASE_URL must be a public HTTPS origin or local HTTP origin.") from exc
        with self.access_control._lock, self.access_control._connect() as conn:
            row = self.access_control._fetchone(conn, """
                SELECT id, name, email, product_updates_opt_in, is_active,
                       email_verification_status, product_updates_opted_in_at, product_updates_consent_version
                FROM users WHERE email = ?
                """, (self.test_recipient,))
        if (row is None or not row["product_updates_opt_in"] or not self.access_control._row_is_active(row)
                or str(row["email_verification_status"] or "") != "verified"):
            raise MarketingRecipientUnavailableError
        if template_id == ORGANIZATION_TEMPLATE_ID and row["product_updates_consent_version"] != 2:
            raise MarketingRecipientUnavailableError
        token = f"{row['id']}.{self._signature(row)}"
        unsubscribe_url = f"{public_base_url.rstrip('/')}/marketing/unsubscribe?{urlencode({'token': token})}"
        name = str(row["name"] or "").strip() or "cliente"
        subject = TEST_TEMPLATE.subject
        text = TEST_TEMPLATE.render(name=name, unsubscribe_url=unsubscribe_url)
        html = None
        idempotency_key = f"{TEST_TEMPLATE_ID}/{request_id}"
        if template_id == ORGANIZATION_TEMPLATE_ID:
            subject, text, html = render_organization_email(name=name, unsubscribe_url=unsubscribe_url)
            subject = f"[TESTE] {subject}"
            idempotency_key = f"marketing-test/{template_id}/{request_id}"
        return await mailer.send_text_email(
            from_email=self.from_email,
            to_email=self.test_recipient,
            subject=subject,
            text=text,
            html=html,
            headers={
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
            idempotency_key=idempotency_key,
        )

    def validate_unsubscribe_token(self, token: str) -> None:
        with self.access_control._lock, self.access_control._connect() as conn:
            self._validate_token(conn, token)

    def unsubscribe(self, token: str) -> None:
        service = self.access_control
        with service._lock, service._connect() as conn:
            row = self._validate_token(conn, token)
            # Keep the original consent evidence; old links stop working after a new opt-in.
            result = service._execute(conn, """
                UPDATE users SET product_updates_opt_in = ?, updated_at = ?
                WHERE id = ? AND email = ? AND COALESCE(product_updates_opted_in_at, '') = ?
                """, (service._false_value(), service.now_provider().isoformat(), row["id"], row["email"],
                      row["product_updates_opted_in_at"] or ""))
            if result.rowcount != 1:
                raise InvalidMarketingUnsubscribeTokenError
            conn.commit()

    def _validate_token(self, conn, token):
        user_id, separator, signature = token.partition(".")
        if not separator or not user_id or len(user_id) > 64 or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise InvalidMarketingUnsubscribeTokenError
        row = self.access_control._fetchone(conn, """
            SELECT id, email, product_updates_opted_in_at FROM users WHERE id = ?
            """, (user_id,))
        if row is None or not hmac.compare_digest(signature, self._signature(row)):
            raise InvalidMarketingUnsubscribeTokenError
        return row

    def _signature(self, row) -> str:
        # A separate purpose prevents unsubscribe links from acting as account/session tokens.
        payload = json.dumps([
            "marketing-unsubscribe-v1", str(row["id"]), str(row["email"]),
            str(row["product_updates_opted_in_at"] or ""),
        ], ensure_ascii=True).encode("utf-8")
        return hmac.new(self.access_control.token_secret, payload, hashlib.sha256).hexdigest()

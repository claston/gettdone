"""Manual marketing delivery; consent and templates are shared with future sequences."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from app.application.access_control import AccessControlService
from app.application.contact_service import ContactDeliveryResult, ContactService

TEST_RECIPIENT = "a@a.com.br"
TEST_TEMPLATE_ID = "marketing-test-v1"
TEST_SUBJECT = "OFX Simples — e-mail de teste"


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


class MarketingEmailService:
    def __init__(self, access_control: AccessControlService):
        self.access_control = access_control

    async def send_test(self, *, mailer: ContactService, public_base_url: str, request_id: str) -> ContactDeliveryResult:
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
                       email_verification_status, product_updates_opted_in_at
                FROM users WHERE email = ?
                """, (TEST_RECIPIENT,))
        if (row is None or not row["product_updates_opt_in"] or not self.access_control._row_is_active(row)
                or str(row["email_verification_status"] or "") != "verified"):
            raise MarketingRecipientUnavailableError
        token = f"{row['id']}.{self._signature(row)}"
        unsubscribe_url = f"{public_base_url.rstrip('/')}/marketing/unsubscribe?{urlencode({'token': token})}"
        return await mailer.send_text_email(
            to_email=TEST_RECIPIENT,
            subject=TEST_TEMPLATE.subject,
            text=TEST_TEMPLATE.render(name=str(row["name"] or "cliente"), unsubscribe_url=unsubscribe_url),
            headers={
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
            idempotency_key=f"{TEST_TEMPLATE_ID}/{request_id}",
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

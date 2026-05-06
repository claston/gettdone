import base64
import os
from dataclasses import dataclass

import httpx

from app.application.errors import ContactDeliveryError, ContactProviderNotConfiguredError, FileTooLargeError


@dataclass(frozen=True)
class ContactAttachment:
    filename: str
    content_type: str
    raw_bytes: bytes


@dataclass(frozen=True)
class ContactMessage:
    name: str
    email: str
    subject: str
    message: str
    attachment: ContactAttachment | None = None


@dataclass(frozen=True)
class ContactDeliveryResult:
    delivery_mode: str
    provider_message_id: str | None = None


def _mask_email(value: str) -> str:
    raw = value.strip()
    if "@" not in raw:
        return "***"
    local, domain = raw.split("@", 1)
    if not local:
        return f"***@{domain}"
    if len(local) == 1:
        return f"{local}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


class ContactService:
    def __init__(
        self,
        *,
        api_key: str | None,
        from_email: str,
        to_email: str,
        dry_run: bool = True,
        max_attachment_bytes: int = 2 * 1024 * 1024,
        resend_api_base: str = "https://api.resend.com",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._from_email = from_email.strip()
        self._to_email = to_email.strip()
        self._dry_run = dry_run
        self._max_attachment_bytes = max_attachment_bytes
        self._resend_api_base = resend_api_base.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @property
    def support_email(self) -> str:
        return self._to_email

    async def deliver(self, contact: ContactMessage) -> ContactDeliveryResult:
        if contact.attachment and len(contact.attachment.raw_bytes) > self._max_attachment_bytes:
            raise FileTooLargeError

        if self._dry_run:
            print(
                "[contact-dry-run]",
                f"name_len={len(contact.name.strip())}",
                f"email={_mask_email(contact.email)}",
                f"subject_len={len(contact.subject.strip())}",
                f"has_attachment={bool(contact.attachment)}",
            )
            return ContactDeliveryResult(delivery_mode="dry_run")

        if not self._api_key:
            raise ContactProviderNotConfiguredError

        payload: dict[str, object] = {
            "from": self._from_email,
            "to": [self._to_email],
            "subject": f"[Contato] {contact.subject}",
            "reply_to": contact.email,
            "text": self._build_text_body(contact),
        }
        if contact.attachment:
            payload["attachments"] = [
                {
                    "filename": contact.attachment.filename,
                    "content": base64.b64encode(contact.attachment.raw_bytes).decode("ascii"),
                }
            ]

        return await self._send_payload(payload)

    async def send_text_email(
        self,
        *,
        to_email: str,
        subject: str,
        text: str,
        reply_to: str | None = None,
    ) -> ContactDeliveryResult:
        clean_to = to_email.strip()
        if not clean_to:
            raise ContactDeliveryError("Missing recipient email.")

        if self._dry_run:
            print(
                "[contact-dry-run]",
                f"to={_mask_email(clean_to)}",
                f"subject_len={len(subject.strip())}",
            )
            return ContactDeliveryResult(delivery_mode="dry_run")

        if not self._api_key:
            raise ContactProviderNotConfiguredError

        payload: dict[str, object] = {
            "from": self._from_email,
            "to": [clean_to],
            "subject": subject.strip() or "Mensagem",
            "text": text.strip() or "Mensagem",
        }
        clean_reply_to = (reply_to or "").strip()
        if clean_reply_to:
            payload["reply_to"] = clean_reply_to

        return await self._send_payload(payload)

    async def _send_payload(self, payload: dict[str, object]) -> ContactDeliveryResult:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._resend_api_base}/emails",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            provider_message = exc.response.text.strip() or "Resend returned an error."
            raise ContactDeliveryError(provider_message) from exc
        except httpx.HTTPError as exc:
            raise ContactDeliveryError("Unable to reach email provider.") from exc

        provider_payload = response.json()
        provider_id = str(provider_payload.get("id") or "").strip() or None
        return ContactDeliveryResult(delivery_mode="resend", provider_message_id=provider_id)

    @staticmethod
    def from_env() -> "ContactService":
        return ContactService(
            api_key=os.getenv("RESEND_API_KEY", ""),
            from_email=os.getenv("CONTACT_FROM_EMAIL", "onboarding@resend.dev"),
            to_email=os.getenv("CONTACT_TO_EMAIL", "suporte@ofxsimples.com"),
            dry_run=_read_env_bool("CONTACT_RESEND_DRY_RUN", default=True),
            max_attachment_bytes=int(os.getenv("CONTACT_ATTACHMENT_MAX_BYTES", str(2 * 1024 * 1024))),
        )

    @staticmethod
    def _build_text_body(contact: ContactMessage) -> str:
        lines = [
            "Novo contato enviado pelo formulario do site.",
            "",
            f"Nome: {contact.name}",
            f"Email: {contact.email}",
            f"Assunto: {contact.subject}",
            "",
            "Mensagem:",
            contact.message,
        ]
        if contact.attachment:
            lines.extend(["", f"Arquivo anexado: {contact.attachment.filename} ({contact.attachment.content_type})"])
        return "\n".join(lines)


def _read_env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    return normalized in {"1", "true", "yes", "on"}

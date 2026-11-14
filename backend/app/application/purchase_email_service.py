import os
import re
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

from app.application.contact_service import ContactDeliveryError, ContactProviderNotConfiguredError
from app.application.purchase_flow import PurchaseFlow

TEMPLATES = Path(__file__).resolve().parents[1] / "email_templates" / "purchase"
NAMES = {"admin_receipt": "05-aviso-administrador", "customer_receipt": "01-pedido-recebido",
         "payment": "02-link-pagamento", "release": "03-plano-liberado", "setup": "04-primeiro-acesso"}


def safe_url(value):
    if not value or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def site_url():
    value = os.getenv("PURCHASE_FRONTEND_URL", "https://www.ofxsimples.com.br").strip().rstrip("/")
    if not safe_url(value):
        raise ValueError("PURCHASE_FRONTEND_URL inválida.")
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}):
        raise ValueError("PURCHASE_FRONTEND_URL deve usar HTTPS, sem query ou fragmento.")
    return value


def render_purchase_email(kind, intent, **extra):
    base = site_url()
    price = int(intent["price_cents"])
    period = {"monthly": "mês"}.get(intent["billing_period"], intent["billing_period"])
    number = os.getenv("WHATSAPP_SUPPORT_NUMBER", "").strip()
    if not re.fullmatch(r"[1-9][0-9]{7,14}", number):
        number = ""
    display = f"+{number}"
    if number.startswith("55") and len(number) == 13:
        display = f"+55 ({number[2:4]}) {number[4:9]}-{number[9:]}"
    values = dict(name=intent["customer_name"], customer_email=intent["customer_email"],
                  customer_whatsapp=intent["customer_whatsapp"], customer_document=intent.get("customer_document") or "Não informado",
                  customer_notes=intent.get("customer_notes") or "Não informadas", order_reference=intent["id"],
                  plan_name=intent["plan_name"], price_label=f"R$ {price // 100},{price % 100:02d}/{period}",
                  site_url=base + "/", login_url=base + "/login.html", admin_order_url=base + "/admin.html#orders",
                  requested_at=intent["created_at"], checkout_terms="Sim — aceite de contato para esta compra"
                  if intent.get("terms_accepted_at") else "Não registrado (pedido anterior)",
                  setup_expiry_label="24 horas", whatsapp_display=display,
                  whatsapp_url=f"https://wa.me/{number}?text=" + quote(f"Olá! Preciso de ajuda com o pedido {intent['id']}."))
    values.update(extra)
    for key, value in values.items():
        if key.endswith("_url") and not safe_url(value):
            raise ValueError(f"URL inválida no template: {key}")
    rendered = {}
    for suffix in ("html", "txt"):
        source = (TEMPLATES / f"{NAMES[kind]}.{suffix}").read_text(encoding="utf-8")
        if not number:
            source = re.sub(r"<!-- support:start -->.*?<!-- support:end -->", "", source, flags=re.S)
            source = re.sub(r"Podemos ajudar\? WhatsApp:.*?\{\{ whatsapp_url \}\}\n", "", source, flags=re.S)

        def substitute(match):
            key = match[1]
            if key not in values:
                raise ValueError(f"Campo não preenchido no template: {key}")
            value = str(values[key])
            return escape(value, quote=True).replace("\n", "<br>") if suffix == "html" else value

        rendered["text" if suffix == "txt" else "html"] = re.sub(r"\{\{\s*(\w+)\s*\}\}", substitute, source)
    subjects = {
        "admin_receipt": f"[Compra] Novo pedido {intent['id']} - {intent['plan_name']}",
        "customer_receipt": f"Recebemos seu pedido do plano {intent['plan_name']}",
        "payment": f"Link de pagamento do plano {intent['plan_name']} - OFX Simples",
        "release": f"Seu plano {intent['plan_name']} está liberado - OFX Simples",
        "setup": "Seu plano foi liberado. Defina sua senha para começar.",
    }
    return {**rendered, "subject": subjects[kind]}


async def deliver_purchase_notification(service, mail, intent_id, kind, *, retry=False):
    flow = PurchaseFlow(service)
    notification = flow.claim_notification(intent_id, kind, retry=retry)
    if not notification:
        row = flow.notification(intent_id, kind)
        return row["status"] if row else "pending"
    token_id = None
    try:
        intent = service.read_checkout_intent_by_id(intent_id=intent_id)
        template = kind
        extra = {}
        if kind == "release" and flow.pending_user(intent["user_id"]):
            template = "setup"
            token_id, token = flow.issue_token(intent_id, "setup")
            extra["setup_url"] = site_url() + "/primeiro-acesso.html#token=" + token
        message = render_purchase_email(template, intent, **extra)
        delivery = await mail.send_text_email(
            to_email=mail.support_email if kind == "admin_receipt" else intent["customer_email"],
            reply_to=intent["customer_email"] if kind == "admin_receipt" else mail.support_email,
            idempotency_key=f"{notification['id']}/{notification['attempts']}", **message,
        )
        sent = delivery.delivery_mode != "dry_run"
        flow.finish_notification(notification, sent=sent, token_id=token_id,
                                 provider_message_id=delivery.provider_message_id)
        return delivery.delivery_mode if sent else "failed"
    except (ContactDeliveryError, ContactProviderNotConfiguredError, ValueError):
        flow.finish_notification(notification, sent=False, token_id=token_id)
        return "failed"

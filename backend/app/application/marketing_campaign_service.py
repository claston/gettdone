"""One manually confirmed guide edition, with resumable, serialized delivery."""

import json
import os
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from app.application.errors import ContactDeliveryError
from app.application.marketing_email_service import MarketingConfigurationError, MarketingEmailService, render_organization_email

CAMPAIGN_ID = "statement-guide-v1"
NAME_MARKER = "__OFX_RECIPIENT_NAME__"
URL_MARKER = "https://example.invalid/__OFX_UNSUBSCRIBE__"


class MarketingCampaignConflict(Exception):
    pass


class MarketingCampaignService:
    def __init__(self, access, admin_id):
        self.access = access
        self.admin_id = admin_id

    def _now(self):
        return self.access.now_provider().astimezone(timezone.utc)

    def _ready(self, conn):
        for table in ("marketing_campaigns", "marketing_campaign_deliveries"):
            if not self.access.db.table_exists(conn, table):
                raise MarketingConfigurationError("Run the manual marketing campaign migration first.")

    def _eligible(self, row):
        return bool(row and row["product_updates_opt_in"] and self.access._row_is_active(row)
                    and row["email_verification_status"] == "verified" and row["product_updates_consent_version"] == 2)

    def _audience(self, conn):
        return self.access._fetchall(conn, """
            SELECT id, name, email, product_updates_opted_in_at FROM users
            WHERE product_updates_opt_in = ? AND is_active = ? AND email_verification_status = 'verified'
              AND product_updates_consent_version = 2 ORDER BY id
        """, (self.access._true_value(), self.access._true_value()))

    def summary(self):
        with self.access._lock, self.access._connect() as conn:
            self._ready(conn)
            campaign = self.access._fetchone(conn, "SELECT * FROM marketing_campaigns WHERE id = ?", (CAMPAIGN_ID,))
            counts = dict.fromkeys(("pending", "sent", "skipped", "review"), 0)
            for row in self.access._fetchall(conn, """
                SELECT status, COUNT(*) AS count FROM marketing_campaign_deliveries WHERE campaign_id = ? GROUP BY status
            """, (CAMPAIGN_ID,)):
                counts[row["status"]] = int(row["count"])
            if campaign:
                subject = campaign["subject"]
            else:
                subject = "Antes de converter: 4 detalhes para conferir no seu extrato"
            review_items = [dict(row) for row in self.access._fetchall(conn, """
                SELECT email, first_attempt_at FROM marketing_campaign_deliveries
                WHERE campaign_id = ? AND status = 'review' ORDER BY user_id LIMIT 200
            """, (CAMPAIGN_ID,))]
            return {"campaign_id": CAMPAIGN_ID, "prepared": campaign is not None, "subject": subject,
                    "eligible": len(self._audience(conn)), "total": sum(counts.values()), **counts,
                    "review_items": review_items,
                    "busy": bool(campaign and campaign["lease_until"] > self._now().isoformat())}

    def prepare(self, *, mailer, expected_eligible):
        current = self.summary()
        if current["prepared"]:
            return current
        default_sender = mailer.require_live_sender()
        sender = os.getenv("MARKETING_FROM_EMAIL", "").strip() or default_sender
        base = os.getenv("MARKETING_PUBLIC_BASE_URL", "https://www.ofxsimples.com.br").strip().rstrip("/")
        try:
            parsed = urlsplit(base)
            local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if ((parsed.scheme != "https" and not local) or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                    or parsed.netloc.endswith(":") or parsed.port == 0):
                raise ValueError
        except ValueError as exc:
            raise MarketingConfigurationError("Invalid public unsubscribe origin.") from exc
        subject, text, html = render_organization_email(name=NAME_MARKER, unsubscribe_url=URL_MARKER)
        now = self._now().isoformat()
        with self.access._lock, self.access._connect() as conn:
            audience = self._audience(conn)
            if not audience or len(audience) != expected_eligible:
                raise MarketingCampaignConflict("A lista mudou ou está vazia. Atualize o resumo antes de preparar o envio.")
            inserted = self.access._execute(conn, """
                INSERT INTO marketing_campaigns
                (id,subject,text_template,html_template,from_email,public_base_url,created_at,created_by)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING
            """, (CAMPAIGN_ID, subject, text, html, sender, base, now, self.admin_id)).rowcount
            if inserted:
                for row in audience:
                    self.access._execute(conn, """
                        INSERT INTO marketing_campaign_deliveries (campaign_id,user_id,email,consent_at,updated_at)
                        VALUES (?,?,?,?,?)
                    """, (CAMPAIGN_ID, row["id"], row["email"], row["product_updates_opted_in_at"] or "", now))
            conn.commit()
        return self.summary()

    def _mark(self, conn, user_id, status, provider_id=None):
        self.access._execute(conn, """
            UPDATE marketing_campaign_deliveries SET status = ?, provider_message_id = ?, updated_at = ?
            WHERE campaign_id = ? AND user_id = ?
        """, (status, provider_id, self._now().isoformat(), CAMPAIGN_ID, user_id))
        conn.commit()

    def _next_payload(self):
        with self.access._lock, self.access._connect() as conn:
            delivery = self.access._fetchone(conn, """
                SELECT * FROM marketing_campaign_deliveries WHERE campaign_id = ? AND status = 'pending' ORDER BY user_id LIMIT 1
            """, (CAMPAIGN_ID,))
            if delivery is None:
                return None
            user_id = delivery["user_id"]
            user = self.access._fetchone(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
            first = delivery["first_attempt_at"]
            eligible = (self._eligible(user) and user["email"] == delivery["email"]
                        and (user["product_updates_opted_in_at"] or "") == delivery["consent_at"])
            if not eligible:
                self._mark(conn, user_id, "review" if first else "skipped")
                return None
            # Resend guarantees idempotency for 24h; stop early rather than risk a duplicate.
            if first and self._now() - datetime.fromisoformat(first) >= timedelta(hours=23):
                self._mark(conn, user_id, "review")
                return None
            if delivery["payload"]:
                return user_id, json.loads(delivery["payload"])
            campaign = self.access._fetchone(conn, "SELECT * FROM marketing_campaigns WHERE id = ?", (CAMPAIGN_ID,))
            marketing = MarketingEmailService(self.access)
            token = f"{user_id}.{marketing._signature(user)}"
            url = f"{campaign['public_base_url']}/marketing/unsubscribe?{urlencode({'token': token})}"
            name = str(user["name"] or "").strip() or "cliente"
            payload = {
                "from_email": campaign["from_email"], "to_email": delivery["email"], "subject": campaign["subject"],
                "text": campaign["text_template"].replace(NAME_MARKER, name).replace(URL_MARKER, url),
                "html": campaign["html_template"].replace(NAME_MARKER, escape(name, quote=True)).replace(
                    URL_MARKER, escape(url, quote=True)),
                "headers": {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
                "idempotency_key": f"marketing-campaign/{CAMPAIGN_ID}/{user_id}",
            }
            self.access._execute(conn, """
                UPDATE marketing_campaign_deliveries SET payload = ?, first_attempt_at = ?, updated_at = ?
                WHERE campaign_id = ? AND user_id = ?
            """, (json.dumps(payload, ensure_ascii=True), self._now().isoformat(), self._now().isoformat(), CAMPAIGN_ID, user_id))
            conn.commit()
            return user_id, payload

    async def send_next(self, *, mailer):
        mailer.require_live_sender()
        lease = str(uuid4())
        now = self._now()
        with self.access._lock, self.access._connect() as conn:
            self._ready(conn)
            claimed = self.access._execute(conn, """
                UPDATE marketing_campaigns SET lease_token = ?, lease_until = ? WHERE id = ? AND lease_until <= ?
            """, (lease, (now + timedelta(minutes=2)).isoformat(), CAMPAIGN_ID, now.isoformat())).rowcount
            conn.commit()
        if not claimed:
            raise MarketingCampaignConflict("Prepare a campanha ou aguarde o envio em andamento. Após interrupção, aguarde até 2 minutos.")
        try:
            item = self._next_payload()
            if item:
                user_id, payload = item
                result = await mailer.send_text_email(**payload)
                if result.delivery_mode != "resend" or not result.provider_message_id:
                    raise ContactDeliveryError("Campaign delivery was not confirmed by the provider.")
                with self.access._lock, self.access._connect() as conn:
                    self._mark(conn, user_id, "sent", result.provider_message_id)
        finally:
            with self.access._lock, self.access._connect() as conn:
                self.access._execute(conn, """
                    UPDATE marketing_campaigns SET lease_token = '', lease_until = '' WHERE id = ? AND lease_token = ?
                """, (CAMPAIGN_ID, lease))
                conn.commit()
        return self.summary()

"""Atomic administrative release. Raw bearer secrets never enter durable storage."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

from app.application.checkout_management import CHECKOUT_STATUS_LOST, OPEN_CHECKOUT_STATUSES
from app.application.plan_management import activate_user_plan


class PurchaseConflict(ValueError):
    pass


class PurchaseFlow:
    def __init__(self, service):
        self.s = service

    def begin(self, conn):
        if not self.s._use_postgres:
            conn.execute("BEGIN IMMEDIATE")

    @property
    def lock_suffix(self):
        return " FOR UPDATE" if self.s._use_postgres else ""

    def event(self, conn, intent_id, event_type, actor_kind, actor_user_id, payload=None):
        messages = {
            "ACCOUNT_CREATED_BY_ADMIN": "Conta de cliente criada pelo administrador; primeiro acesso pendente.",
            "PLAN_RELEASED": "Plano liberado para este pedido.",
            "NOTIFICATION_SENT": "Envio de e-mail aceito pelo provedor.",
            "NOTIFICATION_FAILED": "Falha no envio do e-mail. Reenvio disponível no pedido.",
            "NOTIFICATION_RETRY_REQUESTED": "Reenvio de e-mail solicitado pelo administrador.",
            "INITIAL_ACCESS_COMPLETED": "Primeiro acesso concluído pelo cliente.",
            "SALE_LOST": "Pedido encerrado como venda perdida pelo administrador.",
        }
        self.s._append_checkout_intent_event_with_conn(
            conn, intent_id=intent_id, event_type=event_type, event_message=messages[event_type],
            actor_kind=actor_kind, actor_user_id=actor_user_id, payload=payload or {},
            created_at=self.s.now_provider().isoformat(),
        )

    def lose(self, intent_id, *, actor_kind, actor_user_id):
        s = self.s
        now = s.now_provider().isoformat()
        with s._lock, s._connect() as conn:
            self.begin(conn)
            try:
                intent = s._fetchone(conn, "SELECT status FROM checkout_intents WHERE id = ?" + self.lock_suffix, (intent_id,))
                if intent is None:
                    raise ValueError("Pedido não encontrado.")
                if intent["status"] == CHECKOUT_STATUS_LOST:
                    conn.rollback()
                    return False
                if intent["status"] not in OPEN_CHECKOUT_STATUSES:
                    raise PurchaseConflict("Somente pedidos em aberto podem ser encerrados como venda perdida.")
                sending = s._fetchone(conn, """SELECT id FROM checkout_notifications
                    WHERE intent_id = ? AND status = 'sending' LIMIT 1""", (intent_id,))
                if sending:
                    raise PurchaseConflict("Aguarde o envio de e-mail em andamento antes de encerrar o pedido.")
                s._execute(conn, "UPDATE checkout_intents SET status = ?, updated_at = ? WHERE id = ?",
                           (CHECKOUT_STATUS_LOST, now, intent_id))
                s._execute(conn, """UPDATE purchase_tokens SET invalidated_at = ? WHERE intent_id = ?
                    AND purpose = 'payment' AND invalidated_at IS NULL""", (now, intent_id))
                self.event(conn, intent_id, "SALE_LOST", actor_kind, actor_user_id, {"previous_status": intent["status"]})
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def release(self, intent_id, *, create_account=False, actor_kind, actor_user_id):
        s = self.s
        now = s.now_provider().isoformat()
        with s._lock, s._connect() as conn:
            self.begin(conn)
            try:
                intent = s._fetchone(conn, "SELECT * FROM checkout_intents WHERE id = ?" + self.lock_suffix, (intent_id,))
                if intent is None:
                    raise ValueError("Pedido não encontrado.")
                if intent["status"] == "RELEASED_FOR_USE":
                    conn.rollback()
                    return False
                if intent["status"] not in OPEN_CHECKOUT_STATUSES:
                    raise PurchaseConflict("Pedido encerrado. A liberação não está disponível.")
                payment = s._fetchone(conn, "SELECT * FROM checkout_notifications WHERE intent_id = ? AND kind = 'payment'",
                                     (intent_id,))
                if payment and payment["status"] == "sending":
                    raise PurchaseConflict("Aguarde o envio do link de pagamento antes de liberar o pedido.")
                email = intent["customer_email"].strip().lower()
                # Serialize different orders for the same email before account creation.
                if s._use_postgres:
                    s._execute(conn, "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (email,))
                user = s._fetchone(conn, "SELECT * FROM users WHERE lower(email) = ?" + self.lock_suffix, (email,))
                if intent["user_id"] and (user is None or user["id"] != intent["user_id"]):
                    raise PurchaseConflict("O e-mail do pedido difere da conta vinculada. Revise os dados antes de liberar.")
                if user is None:
                    if not create_account:
                        raise PurchaseConflict("Confirme a criação da conta para o e-mail informado ao liberar este pedido.")
                    user_id = f"usr_{uuid4().hex[:12]}"
                    salt = secrets.token_hex(16)
                    password_hash = s._hash_password(password=secrets.token_urlsafe(48), salt=salt)
                    # ON CONFLICT handles a concurrent public registration. Never overwrite credentials.
                    s._execute(conn, """INSERT INTO users (
                        id, name, email, is_admin, is_active, password_hash, password_salt, auth_provider,
                        initial_access_pending, account_creation_source, email_verification_status,
                        product_updates_opt_in, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'local', ?, 'checkout_admin', 'pending', ?, ?, ?)
                        ON CONFLICT(email) DO NOTHING""",
                        (user_id, intent["customer_name"], email, s._false_value(), s._false_value(),
                         password_hash, salt, s._true_value(), s._false_value(), now, now))
                    user = s._fetchone(conn, "SELECT * FROM users WHERE email = ?" + self.lock_suffix, (email,))
                    if user["id"] == user_id:
                        self.event(conn, intent_id, "ACCOUNT_CREATED_BY_ADMIN", actor_kind, actor_user_id,
                                   {"user_id": user_id})
                if not user["initial_access_pending"] and not s._row_is_active(user):
                    raise PurchaseConflict("A conta está suspensa. Revise a situação antes de liberar o pedido.")
                user_id = user["id"]
                activate_user_plan(conn, fetchone=s._fetchone, execute=s._execute, true_value=s._true_value(),
                                   user_id=user_id, plan_code=intent["plan_code"], now_iso=now,
                                   subscription_id=f"sub_{uuid4().hex[:16]}")
                s._execute(conn, """UPDATE checkout_intents SET user_id = ?, status = 'RELEASED_FOR_USE',
                    released_at = ?, updated_at = ? WHERE id = ?""", (user_id, now, now, intent_id))
                self.event(conn, intent_id, "PLAN_RELEASED", actor_kind, actor_user_id,
                           {"user_id": user_id, "plan_code": intent["plan_code"]})
                self.enqueue(conn, intent_id, "release")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        s._invalidate_active_plan_cache(user_id)
        return True

    def enqueue(self, conn, intent_id, kind):
        now = self.s.now_provider().isoformat()
        self.s._execute(conn, """INSERT INTO checkout_notifications (id, intent_id, kind, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(intent_id, kind) DO NOTHING""",
            (f"ntf_{uuid4().hex}", intent_id, kind, now, now))

    def notification(self, intent_id, kind="release"):
        with self.s._lock, self.s._connect() as conn:
            row = self.s._fetchone(conn, "SELECT * FROM checkout_notifications WHERE intent_id = ? AND kind = ?",
                                  (intent_id, kind))
            return dict(row) if row else None

    def claim_notification(self, intent_id, kind, *, retry=False):
        s = self.s
        now = s.now_provider()
        with s._lock, s._connect() as conn:
            self.begin(conn)
            try:
                intent = s._fetchone(conn, "SELECT status FROM checkout_intents WHERE id = ?" + self.lock_suffix, (intent_id,))
                if (not intent or intent["status"] == CHECKOUT_STATUS_LOST
                        or (kind == "payment" and intent["status"] not in OPEN_CHECKOUT_STATUSES)):
                    conn.rollback()
                    return None
                self.enqueue(conn, intent_id, kind)
                row = s._fetchone(conn, "SELECT * FROM checkout_notifications WHERE intent_id = ? AND kind = ?"
                                 + self.lock_suffix, (intent_id, kind))
                busy = row["status"] == "sending" and s._parse_usage_datetime(row["updated_at"], fallback=now) > now - timedelta(minutes=5)
                if busy or (not retry and row["status"] != "pending"):
                    conn.rollback()
                    return None
                s._execute(conn, """UPDATE checkout_notifications SET status = 'sending', attempts = attempts + 1,
                    updated_at = ? WHERE id = ?""", (now.isoformat(), row["id"]))
                conn.commit()
                return {**dict(row), "attempts": int(row["attempts"]) + 1}
            except Exception:
                conn.rollback()
                raise

    def notification_states(self, intent_ids):
        if not intent_ids:
            return {}
        placeholders = ",".join("?" for _ in intent_ids)
        with self.s._lock, self.s._connect() as conn:
            rows = self.s._fetchall(conn, f"SELECT intent_id, kind, status FROM checkout_notifications WHERE intent_id IN ({placeholders})",
                                   tuple(intent_ids))
        return {(row["intent_id"], row["kind"]): row["status"] for row in rows}

    def finish_notification(self, notification, *, sent, provider_message_id=None, token_id=None):
        s = self.s
        now = s.now_provider().isoformat()
        with s._lock, s._connect() as conn:
            self.begin(conn)
            s._fetchone(conn, "SELECT id FROM checkout_intents WHERE id = ?" + self.lock_suffix,
                        (notification["intent_id"],))
            s._execute(conn, """UPDATE checkout_notifications SET status = ?, provider_message_id = ?, updated_at = ?
                WHERE id = ? AND attempts = ?""", ("sent" if sent else "failed", provider_message_id, now,
                                                    notification["id"], notification["attempts"]))
            if not sent and token_id:
                s._execute(conn, "UPDATE purchase_tokens SET invalidated_at = ? WHERE id = ?", (now, token_id))
            self.event(conn, notification["intent_id"], "NOTIFICATION_SENT" if sent else "NOTIFICATION_FAILED",
                       "system", None, {"kind": notification["kind"]})
            conn.commit()

    def issue_token(self, intent_id, purpose):
        s = self.s
        now = s.now_provider()
        with s._lock, s._connect() as conn:
            self.begin(conn)
            try:
                intent = s._fetchone(conn, "SELECT * FROM checkout_intents WHERE id = ?" + self.lock_suffix, (intent_id,))
                if intent is None:
                    raise ValueError("Pedido não encontrado.")
                if purpose == "setup":
                    user = s._fetchone(conn, "SELECT * FROM users WHERE id = ?" + self.lock_suffix, (intent["user_id"],))
                    if not user or not user["initial_access_pending"] or intent["status"] != "RELEASED_FOR_USE":
                        raise ValueError("Primeiro acesso indisponível para esta conta.")
                    s._execute(conn, """UPDATE purchase_tokens SET invalidated_at = ? WHERE user_id = ?
                        AND purpose = 'setup' AND consumed_at IS NULL AND invalidated_at IS NULL""", (now.isoformat(), user["id"]))
                else:
                    if purpose != "payment" or intent["status"] not in OPEN_CHECKOUT_STATUSES:
                        raise ValueError("Link de pagamento indisponível.")
                    # Previous links remain usable and always resolve to the current, stored URL.
                token, token_id = secrets.token_urlsafe(32), f"pt_{uuid4().hex}"
                s._execute(conn, """INSERT INTO purchase_tokens
                    (id, intent_id, user_id, purpose, token_hash, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""", (token_id, intent_id, intent["user_id"], purpose,
                    hashlib.sha256(token.encode()).hexdigest(), now.isoformat(),
                    (now + timedelta(hours=24 if purpose == "setup" else 168)).isoformat()))
                conn.commit()
                return token_id, token
            except Exception:
                conn.rollback()
                raise

    def pending_user(self, user_id):
        with self.s._lock, self.s._connect() as conn:
            row = self.s._fetchone(conn, "SELECT initial_access_pending FROM users WHERE id = ?", (user_id,))
            return bool(row and row["initial_access_pending"])

    def _valid_token(self, conn, token, purpose):
        row = self.s._fetchone(conn, "SELECT * FROM purchase_tokens WHERE token_hash = ? AND purpose = ?",
                              (hashlib.sha256(token.encode()).hexdigest(), purpose))
        now = self.s.now_provider()
        if (not row or row["consumed_at"] or row["invalidated_at"]
                or self.s._parse_usage_datetime(row["expires_at"], fallback=now) <= now):
            raise ValueError("Link inválido ou expirado. Solicite um novo link à equipe.")
        return row

    def payment_page(self, token):
        with self.s._lock, self.s._connect() as conn:
            row = self._valid_token(conn, token, "payment")
            intent = self.s._fetchone(conn, "SELECT * FROM checkout_intents WHERE id = ?", (row["intent_id"],))
            if not intent["payment_link"] or intent["status"] != "AWAITING_PAYMENT":
                raise ValueError("O pagamento deste pedido não está disponível.")
            return {"payment_link": intent["payment_link"], "order_reference": intent["id"], "plan_name": intent["plan_name"]}

    def confirm(self, *, token, password, accepted_terms):
        if not accepted_terms:
            raise ValueError("Aceite os Termos de Uso e a Política de Privacidade para continuar.")
        if not 8 <= len(password) <= 256:
            raise ValueError("A senha deve conter entre 8 e 256 caracteres.")
        s = self.s
        now = s.now_provider().isoformat()
        with s._lock, s._connect() as conn:
            self.begin(conn)
            try:
                row = self._valid_token(conn, token, "setup")
                # Release and reissue also lock the order before its user. The event FK
                # needs the order lock, so taking only the user first can deadlock.
                s._fetchone(conn, "SELECT id FROM checkout_intents WHERE id = ?" + self.lock_suffix,
                            (row["intent_id"],))
                user = s._fetchone(conn, "SELECT * FROM users WHERE id = ?" + self.lock_suffix, (row["user_id"],))
                row = self._valid_token(conn, token, "setup")
                if not user or not user["initial_access_pending"] or user["auth_provider"] != "local":
                    raise ValueError("Primeiro acesso indisponível para esta conta.")
                salt = secrets.token_hex(16)
                s._execute(conn, """UPDATE users SET password_hash = ?, password_salt = ?, is_active = ?,
                    initial_access_pending = ?, email_verification_status = 'verified', email_verified_at = ?,
                    terms_accepted_at = ?, privacy_accepted_at = ?, updated_at = ?, credential_version = credential_version + 1
                    WHERE id = ?""", (s._hash_password(password=password, salt=salt), salt, s._true_value(),
                                       s._false_value(), now, now, now, now, user["id"]))
                s._execute(conn, "UPDATE purchase_tokens SET consumed_at = ? WHERE id = ?", (now, row["id"]))
                s._execute(conn, """UPDATE purchase_tokens SET invalidated_at = ? WHERE user_id = ?
                    AND purpose = 'setup' AND consumed_at IS NULL AND invalidated_at IS NULL""", (now, user["id"]))
                self.event(conn, row["intent_id"], "INITIAL_ACCESS_COMPLETED", "customer", user["id"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise

"""Private, versioned pilot configuration. No price or allowance is seeded."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.application.office_documents import fingerprint
from app.application.plan_management import activate_user_plan

OFFICE_PLAN = 'escritorio_documentos'


def office_commercial(access, *, actor_kind, actor_user_id, operation_id, action, values):
    signature = fingerprint([action, values])
    now = datetime.now(timezone.utc).isoformat()
    renewal = False
    with access._lock, access.db.connect() as conn:
        if not access._use_postgres:
            conn.execute('BEGIN IMMEDIATE')
        else:
            access.db.execute(conn, "SELECT pg_advisory_xact_lock(783221)")
        prior = access.db.fetchone(conn, 'SELECT * FROM office_commercial_events WHERE operation_id = ?', (operation_id,))
        if prior:
            if prior['request_hash'] != signature:
                raise ValueError('Esta operação já foi utilizada com outros dados.')
            return json.loads(prior['result_json'])
        if action == 'configure':
            version = values['version']
            if access.db.fetchone(conn, 'SELECT id FROM plan_versions WHERE code = ? AND version = ?', (OFFICE_PLAN, version)):
                raise ValueError('A versão já existe. Cadastre uma nova versão sem alterar contratos vigentes.')
            active = values.get('price_cents') is not None and values.get('quota_limit') is not None
            access.db.execute(conn, '''INSERT INTO plan_versions
                (id, code, name, version, currency, price_cents, billing_period, quota_mode, quota_limit,
                 quota_window_days, max_upload_size_bytes, max_pages_per_file, max_pages_per_file_ocr,
                 batch_conversion_enabled, batch_max_files, is_public, is_active, created_at)
                VALUES (?, ?, ?, ?, 'BRL', ?, 'monthly', 'document_conversion', ?, 30, 10485760, ?, ?, ?, ?, ?, ?, ?)''',
                (f'plan_{OFFICE_PLAN}_v{version}', OFFICE_PLAN, 'Escritório Documentos', version,
                 values.get('price_cents') or 0, values.get('quota_limit') or 0, values['max_pages_per_file'],
                 values['max_pages_per_file_ocr'], access._true_value(), values['batch_max_files'],
                 False if access._use_postgres else 0, active if access._use_postgres else int(active), now))
            result = {'code': OFFICE_PLAN, 'version': version, 'active': active, 'public': False}
        else:
            user = values['user_id']
            if action == 'activate':
                renewal = bool(access.db.fetchone(conn, '''SELECT s.id FROM user_plan_subscriptions s
                    JOIN plan_versions p ON p.id = s.plan_version_id WHERE s.user_id = ? AND p.code = ? LIMIT 1''',
                    (user, OFFICE_PLAN)))
                expires = datetime.fromisoformat(values['expires_at'])
                if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                    raise ValueError('Informe uma data futura para o fim do acesso contratado.')
                result = activate_user_plan(conn, fetchone=access.db.fetchone, execute=access.db.execute,
                    true_value=access._true_value(), user_id=user, plan_code=OFFICE_PLAN, now_iso=now,
                    subscription_id=f'sub_{uuid4().hex}')
                access.db.execute(conn, "UPDATE user_plan_subscriptions SET ended_at = ? WHERE user_id = ? AND status = 'active'",
                                  (expires.astimezone(timezone.utc).isoformat(), user))
                result = {**result, 'user_id': user, 'expires_at': expires.isoformat()}
            else:
                access.db.execute(conn, '''UPDATE user_plan_subscriptions SET status = 'ended', ended_at = ?
                    WHERE user_id = ? AND status = 'active' AND plan_version_id IN
                    (SELECT id FROM plan_versions WHERE code = ?)''', (now, user, OFFICE_PLAN))
                result = {'user_id': user, 'status': 'ended'}
        access.db.execute(conn, '''INSERT INTO office_commercial_events
            (operation_id, request_hash, actor_kind, actor_user_id, event_type, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)''', (operation_id, signature, actor_kind, actor_user_id, action, json.dumps(result), now))
        conn.commit()
    if action != 'configure':
        access._invalidate_active_plan_cache(values['user_id'])
        from app.application.office_telemetry import office_event
        event = ('office_renewed' if renewal else 'office_activated') if action == 'activate' else 'office_cancelled'
        office_event(access, values['user_id'], event)
    return result

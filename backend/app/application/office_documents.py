"""Operational dates and manual verification, separate from bank review."""
import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, timezone

from app.application.pro_organization import OrganizationConflict, OrganizationService


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def source_payload(payload, *, legacy=False):
    """Lifetime and review acknowledgements are not changes to extracted evidence."""
    result = deepcopy(payload)
    if not isinstance(result, dict):
        return result
    analysis = result.get('analysis')
    if isinstance(analysis, dict):
        original = analysis.pop('_retention_original_expires_at', None)
        analysis.pop('retention_job_id', None)
        if legacy and original:
            analysis['expires_at'] = original
        elif not legacy:
            analysis.pop('expires_at', None)
    if not legacy:
        result.pop('reviewed_at', None)
    return result


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def document_period(analysis):
    financial = analysis.get('financial_document')
    if financial is not None:
        dates = {_date(value) for row in financial.get('observations', []) for value in row.get('dates', {}).values()}
        dates.discard(None)
        return (next(iter(dates)),) * 2 if len(dates) == 1 else (None, None)
    dates = [_date(row.get('date')) for row in analysis.get('preview_transactions', [])]
    dates = [value for value in dates if value]
    return (min(dates), max(dates)) if dates else (None, None)


def matches_month(analysis, month):
    if not month:
        return True
    start, end = document_period(analysis)
    if month == 'unknown':
        return start is None
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        raise ValueError('Informe o mês no formato AAAA-MM.')
    return bool(start and start[:7] <= month <= end[:7])


def record_source(record, context, *, legacy=False):
    return fingerprint({'payload': source_payload(record.result.payload, legacy=legacy) if record.result else None,
                        'status': str(record.status), 'sha256': record.job.document.sha256_hex,
                        'context': context, 'updated_at': record.updated_at})


class OfficeReviews(OrganizationService):
    def prefetch(self, owner, job_ids):
        """One bounded read per list/package; no connection per document."""
        placeholders = ','.join('?' for _ in job_ids)
        self._cached_owner, self._review_rows, self._associations = owner, {}, set()
        if not job_ids:
            return
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            params = (organization, self._now(), *job_ids)
            for row in self.db.fetchall(conn, f'''SELECT * FROM pro_document_reviews
                    WHERE organization_id = ? AND expires_at > ? AND job_id IN ({placeholders})''', params):
                self._review_rows[row['job_id']] = dict(row)
            for row in self.db.fetchall(conn, f'''SELECT job_id, version FROM office_document_associations
                    WHERE organization_id = ? AND expires_at > ? AND job_id IN ({placeholders})''', params):
                self._associations.add((row['job_id'], row['version']))

    def associate(self, conn, owner, job_id, context, expires_at):
        organization = self._organization(conn, owner)
        self._write(conn, '''INSERT INTO office_document_associations
            (organization_id, job_id, version, client_id, actor, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (organization, job_id, context['version'], context['client_id'], owner, self._now(), expires_at.isoformat()))

    def manual_association(self, owner, job_id, version):
        if getattr(self, '_cached_owner', None) == owner:
            return (job_id, version) in self._associations
        with self.db.connect() as conn:
            return bool(self.db.fetchone(conn, '''SELECT version FROM office_document_associations
                WHERE organization_id = ? AND job_id = ? AND version = ? AND expires_at > ?''',
                (self._organization(conn, owner), job_id, version, self._now())))

    def read(self, owner, job_id, source, *, legacy_source=None):
        if getattr(self, '_cached_owner', None) == owner:
            row = self._review_rows.get(job_id)
        else:
            with self.db.connect() as conn:
                organization = self._organization(conn, owner)
                row = self.db.fetchone(conn, '''SELECT * FROM pro_document_reviews
                    WHERE organization_id = ? AND job_id = ? AND expires_at > ?''', (organization, job_id, self._now()))
        if row and row['expires_at'] <= self._now():
            row = None
        if row is None:
            return {'status': 'pending', 'version': 0, 'document_type': None, 'actor': None, 'updated_at': None}
        result = {key: row[key] for key in ('status', 'version', 'document_type', 'actor', 'updated_at')}
        if row['source'] not in {source, legacy_source}:
            result['status'] = 'pending'
        return result

    def save(self, owner, job_id, source, expires_at, status, document_type, expected_version, operation_id):
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError('O prazo de acesso a este documento terminou.')
        request_hash = fingerprint([job_id, source, status, document_type, expected_version])
        with self.db.connect() as conn:
            organization = self._organization(conn, owner, create=True)
            self._write(conn, 'UPDATE pro_organizations SET created_at = created_at WHERE id = ?', (organization,))
            prior = self.db.fetchone(conn, '''SELECT * FROM pro_document_review_events
                WHERE organization_id = ? AND operation_id = ? AND expires_at > ?''', (organization, operation_id, self._now()))
            if prior:
                if prior['request_hash'] != request_hash:
                    raise OrganizationConflict('Esta confirmação já foi usada. Atualize a lista e tente novamente.')
                return json.loads(prior['result_json'])
            row = self.db.fetchone(conn, 'SELECT version FROM pro_document_reviews WHERE organization_id = ? AND job_id = ?',
                                   (organization, job_id))
            version = int(row['version']) if row else 0
            if version != expected_version:
                raise OrganizationConflict('O documento mudou. Atualize a lista antes de confirmar.')
            now = self._now()
            result = {'status': status, 'version': version + 1, 'document_type': document_type, 'actor': owner, 'updated_at': now}
            self._write(conn, '''INSERT INTO pro_document_reviews
                (organization_id, job_id, source, version, status, document_type, actor, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (organization_id, job_id) DO UPDATE SET
                source=excluded.source, version=excluded.version, status=excluded.status, document_type=excluded.document_type,
                actor=excluded.actor, updated_at=excluded.updated_at, expires_at=excluded.expires_at''',
                (organization, job_id, source, version + 1, status, document_type, owner, now, expires_at.isoformat()))
            self._write(conn, '''INSERT INTO pro_document_review_events
                (organization_id, operation_id, job_id, request_hash, result_json, expires_at) VALUES (?, ?, ?, ?, ?, ?)''',
                (organization, operation_id, job_id, request_hash, json.dumps(result), expires_at.isoformat()))
            conn.commit()
            return result

    def purge(self, *, now=None):
        before = (now or datetime.now(timezone.utc)).isoformat()
        with self.db.connect() as conn:
            for table in ('pro_document_link_events', 'pro_document_movement_links',
                          'pro_document_review_events', 'pro_document_reviews', 'office_document_associations'):
                if not self.db.table_exists(conn, table):
                    continue
                self._write(conn, f'DELETE FROM {table} WHERE expires_at <= ?', (before,))
            conn.commit()

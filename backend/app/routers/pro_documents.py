"""Document navigation over existing conversion records; no new copies or retention."""

import base64
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.application.office_documents import OfficeReviews, matches_month
from app.dependencies import get_async_conversion_report_service, get_conversion_batch_service, get_report_service
from app.routers.office_documents import current_record, describe
from app.routers.pro_organization import decode_cursor, organization_owner, organization_service, owned_snapshot

router = APIRouter(prefix="/api/pro/documents", tags=["PRO documents"])


def document_category(payload):
    analysis = payload.get("analysis") or {}
    kind = analysis.get("document_type") or payload.get("document_type")
    if kind in {"bank_statement", "credit_card_statement", "prepaid_card_statement"}:
        return "statements"
    if kind in {"bank_receipt", "notary_receipt"}:
        return "receipts"
    if kind == "fiscal_invoice":
        return "invoices"
    return "unclassified"


@router.get("")
def documents(
    owner=Depends(organization_owner),
    service=Depends(organization_service),
    batches=Depends(get_conversion_batch_service),
    client_id: str | None = Query(None, max_length=100),
    batch_id: str | None = Query(None, max_length=100),
    unassigned: bool = False,
    category: Literal["all", "statements", "receipts", "invoices", "unclassified"] = "all",
    cursor: str | None = Query(None, max_length=500),
    limit: int = Query(30, ge=1, le=50),
    month: str = '',
    account_id: str = '',
    report=Depends(get_report_service),
    async_report=Depends(get_async_conversion_report_service),
):
    if month:
        try:
            matches_month({}, month)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if unassigned and client_id:
        raise HTTPException(400, "Escolha um cliente ou o filtro Sem cliente associado.")
    if batches is None:
        raise HTTPException(503, "Não foi possível consultar os documentos agora. Tente novamente.")
    if client_id:
        with service.db.connect() as conn:
            try:
                service._entity(conn, "pro_clients", service._organization(conn, owner), client_id)
            except KeyError:
                raise HTTPException(404, "Cliente não encontrado.") from None
    if account_id:
        with service.db.connect() as conn:
            try:
                account = service._entity(conn, 'pro_accounts', service._organization(conn, owner), account_id)
                if client_id and account['client_id'] != client_id:
                    raise KeyError
            except KeyError:
                raise HTTPException(404, 'Conta não encontrada para este cliente.') from None
    if batch_id:
        owned_snapshot(batches, owner, batch_id)
    before = decode_cursor(cursor)
    now = datetime.now(timezone.utc)
    items = []
    next_cursor = None
    # A bounded source page keeps sparse category filters affordable. An empty
    # filtered page can have a continuation; clients must expose that explicitly.
    rows = batches.repository.list_files_for_owner(identity_type="user", identity_id=owner, limit=100, before=before, active_only=True)
    contexts = service.contexts(owner, [row.job_id for row in rows])
    reviews = OfficeReviews(service.db)
    reviews.prefetch(owner, [row.job_id for row in rows])
    for index, row in enumerate(rows):
        context = contexts.get(row.job_id, {"client_id": None, "account_id": None, "period_ids": [], "version": 0})
        matches = ((not batch_id or row.batch_id == batch_id)
                   and (not client_id or context["client_id"] == client_id)
                   and (not account_id or context['account_id'] == account_id)
                   and (not unassigned or not context["client_id"]))
        if matches:
            expired = row.expires_at <= now or row.status == "expired"
            available = not expired
            expires_at = row.expires_at
            status = row.status
            payload = {}
            office = {}
            if not expired:
                record = batches.repository.get(row.job_id)
                if record and record.job.identity.identity_type == "user" and record.job.identity.identity_id == owner:
                    expires_at = min(expires_at, record.expires_at)
                    status = str(getattr(record.status, "value", record.status))
                    expired = expires_at <= now or status == "expired"
                    available = not expired
                    if available:
                        record = current_record(record, owner, (async_report, report))
                        expires_at = min(expires_at, record.expires_at)
                        expired = record.expires_at <= now or str(getattr(record.status, 'value', record.status)) == 'expired'
                        available = not expired
                        payload = (record.result.payload or {}) if record.result else {}
                        if available:
                            office = describe(record, contexts.get(row.job_id, {}), reviews, owner)
                            office.pop('analysis')
                else:
                    available = False
                    status = "unavailable"
            kind = document_category(payload)
            if office:
                kind = 'statements' if office['is_bank'] else document_category({'analysis': {'document_type': office['document_type']}})
            if not expired and (category == "all" or kind == category) and matches_month(payload.get('analysis') or {}, month):
                items.append({
                    "job_id": row.job_id, "batch_id": row.batch_id, "filename": row.filename,
                    "status": "expired" if expired else status, "is_expired": expired,
                    "created_at": row.created_at.isoformat(), "expires_at": expires_at.isoformat(),
                    "context": context, "category": kind,
                    "movement_start_date": None, "movement_end_date": None,
                    "review_url": f"/review/{row.batch_id}/{row.job_id}" if available else None,
                    **office,
                })
        if len(items) == limit or index == len(rows) - 1:
            if index < len(rows) - 1 or len(rows) == 100:
                next_cursor = base64.urlsafe_b64encode(json.dumps([row.created_at.isoformat(), row.job_id]).encode()).decode().rstrip("=")
            break
    return {"items": items, "next_cursor": next_cursor}

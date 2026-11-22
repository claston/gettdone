"""Reversible classification changes, within the existing review transaction/lock."""

import hashlib
import json
from copy import deepcopy

from app.application.document_evidence import EVIDENCE_TYPES, parse_evidence_pages
from app.application.errors import AnalysisEditConflictError

_PROJECTION = (
    "document_type",
    "financial_document",
    "preview_transactions",
    "report_transactions",
    "transactions_total",
    "total_inflows",
    "total_outflows",
    "net_total",
    "opening_balance",
    "closing_balance",
    "statement_balance_check",
    "matched_groups",
    "reversed_entries",
    "potential_duplicates",
)


def correct_document_type(service, content, *, document_type, actor_id, operation_id, expected_updated_at):
    from app.application.review_commands import ReviewCommandResult

    if document_type not in EVIDENCE_TYPES | {"bank_statement"} or not actor_id or not operation_id:
        raise ValueError("Informe o tipo do documento e a identificação da alteração.")
    digest = hashlib.sha256(json.dumps([document_type, actor_id, expected_updated_at]).encode()).hexdigest()
    previous_operation = (content.get("edit_operations") or {}).get(operation_id)
    if previous_operation:
        if previous_operation["hash"] != digest:
            raise AnalysisEditConflictError
        service.confirm(content, operation_id)
        return ReviewCommandResult(replayed=True)
    if not expected_updated_at or content.get("updated_at") != expected_updated_at:
        raise AnalysisEditConflictError
    if content.get("file_type") != "pdf":
        raise ValueError("A alteração de tipo está disponível para documentos PDF.")
    previous = content.get("document_type") or "unknown"
    if previous not in EVIDENCE_TYPES | {"bank_statement", "unknown"}:
        raise ValueError("Este relatório tem uma conferência específica e não permite alterar o tipo.")
    if document_type == previous:
        raise ValueError("O documento já está classificado com este tipo.")
    if (document_type == "bank_statement" and not content.get("document_type_bank_snapshot")
            and not (previous == 'unknown' and not content.get('financial_document') and content.get('report_transactions'))):
        raise ValueError("Este documento não possui uma leitura de extrato para restaurar. A alteração exige reprocessar o arquivo.")
    content.setdefault("document_type_automatic", previous)
    if document_type == "bank_statement":
        content.update(deepcopy(content.get("document_type_bank_snapshot", {})))
        content["document_type"] = document_type
    else:
        if previous not in EVIDENCE_TYPES:
            content["document_type_bank_snapshot"] = {
                key: deepcopy(content.get(key)) for key in _PROJECTION if key in content or key == "financial_document"
            }
        financial = parse_evidence_pages([], document_type=document_type)
        old_financial = content.get("financial_document") or {}
        if old_financial:
            financial["observations"] = deepcopy(old_financial.get("observations", []))
            for key in ("source_document_sha256", "representation", "extraction_method", "classification"):
                if key in old_financial:
                    financial[key] = deepcopy(old_financial[key])
        else:
            # A correction does not reinterpret an old signed row as a payment.
            # Show it explicitly as previous extraction evidence pending review.
            financial["observations"] = [
                {
                    "observation_type": "previous_extraction",
                    "dates": {},
                    "amounts": {},
                    "attributes": {
                        "label": "Leitura anterior — confira no original",
                        "value": f"{row.get('date', '')} · {row.get('description', '')} · {row.get('amount', '')}",
                    },
                    "source_refs": [{"page": row["source_page"], "line": row.get("source_line") or 1}] if row.get("source_page") else [],
                }
                for row in content.get("report_transactions", [])
                if not row.get("is_deleted")
            ]
        financial["notice"] += " O tipo foi corrigido manualmente; os campos da leitura anterior precisam de conferência no original."
        content["financial_document"] = financial
        for key in ("preview_transactions", "report_transactions"):
            for row in content.get(key, []):
                row["is_deleted"] = True
        for key in (
            "transactions_total",
            "total_inflows",
            "total_outflows",
            "net_total",
            "matched_groups",
            "reversed_entries",
            "potential_duplicates",
        ):
            content[key] = 0
        for key in ("opening_balance", "closing_balance", "statement_balance_check"):
            content[key] = None
        content["document_type"] = document_type
    content.pop("reviewed_at", None)
    now = service.now_provider().isoformat()
    content["updated_at"] = now
    content.setdefault("document_type_history", []).append(
        {
            "before": previous,
            "after": document_type,
            "actor_id": actor_id,
            "changed_at": now,
            "operation_id": operation_id,
            "reason": "manual_document_type_correction",
        }
    )
    content.setdefault("edit_operations", {})[operation_id] = {"hash": digest, "updated_at": now}
    preview = service._parse_transaction_rows(content.get("preview_transactions", []))
    report = service._parse_transaction_rows(content.get("report_transactions", []))
    service._refresh_structured_result_content(content, preview_rows=preview, report_rows=report)
    return ReviewCommandResult(publish_artifacts=True, metadata_changed=True, preview_rows=preview, report_rows=report)

"""Single boundary for legacy transaction edits, export settings and review.

The service changes an in-memory legacy document. Storage adapters retain their
existing lock/load/publish order, TTL and authority; no canonical store is added.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError, InvalidFileContentError
from app.application.legacy_analysis_state import LegacyAnalysisState, _is_confirmed_empty_statement
from app.application.models import TransactionRow
from app.application.source_regions import validate_review_regions
from app.application.statement_account_export import assert_account_overrides, refresh_edited_account_rows, statement_scopes

if TYPE_CHECKING:
    from app.application.conversion.contracts.batches import ConversionBatchRepository


@dataclass
class ReviewCommandResult:
    publish_artifacts: bool = False
    replayed: bool = False
    metadata_changed: bool = False
    preview_rows: list[TransactionRow] = field(default_factory=list)
    report_rows: list[TransactionRow] = field(default_factory=list)
    effective_overrides: dict[str, float | str | None] = field(default_factory=dict)


class ReviewCommandService(LegacyAnalysisState):
    """Mutate detached legacy state; callers discard it on error and own I/O.

    HTTP authorization remains at the existing routes. Adapters must hold their
    analysis lock across load, command and publication, including remote upload.
    This is deliberately not a distributed CAS or a new persistence authority.
    """

    def __init__(self, now_provider: Callable[[], datetime] | None = None) -> None:
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def confirm(self, content: dict, operation_id: str, *, analysis_id: str | None = None) -> dict[str, object] | None:
        receipt = (content.get("edit_operations") or {}).get(operation_id)
        if receipt is None:
            return None
        if receipt["updated_at"] != content.get("updated_at"):
            raise AnalysisEditConflictError
        return self._edit_result(analysis_id if analysis_id is not None else str(content.get("analysis_id") or ""), content, operation_id)

    def mark_reviewed(self, repository: ConversionBatchRepository, job_id: str) -> str:
        reviewed_at = self.now_provider().isoformat()
        repository.mark_reviewed(job_id, reviewed_at=reviewed_at)
        return reviewed_at

    def apply(
        self,
        content: dict[str, object],
        edits: list[dict[str, object]],
        expected_updated_at: str | None = None,
        opening_balance: float | None = None,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
        operation_id: str | None = None,
    ) -> ReviewCommandResult:
        if content.get("financial_document") is not None:
            raise InvalidFileContentError(
                "A edição de relatórios especializados ainda não está disponível. "
                "Os campos extraídos estão preservados no CSV e no Excel."
            )

        operation_hash = hashlib.sha256(json.dumps({
            "edits": edits, "expected_updated_at": expected_updated_at,
            "opening_balance": opening_balance, "closing_balance": closing_balance,
            "bank_branch": bank_branch, "account_number": account_number, "bank_code": bank_code,
        }, sort_keys=True).encode()).hexdigest()
        receipts = content.get("edit_operations") or {}
        if operation_id and operation_id in receipts:
            if receipts[operation_id]["hash"] != operation_hash:
                raise AnalysisEditConflictError
            self.confirm(content, operation_id)
            return ReviewCommandResult(replayed=True)

        preview_rows = self._parse_transaction_rows(content.get("preview_transactions", []))
        report_rows_raw = content.get("report_transactions") or content.get("preview_transactions", [])
        report_rows = self._parse_transaction_rows(report_rows_raw)
        current_updated_at = str(content.get("updated_at") or "").strip()
        assert_account_overrides(report_rows, opening_balance=opening_balance, closing_balance=closing_balance,
                                 bank_branch=bank_branch, account_number=account_number, bank_code=bank_code,
                                 statement_accounts=statement_scopes(content.get("document_parts")))

        if (len(preview_rows) == 0 or len(report_rows) == 0) and not _is_confirmed_empty_statement(content):
            raise AnalysisNotFoundError
        if expected_updated_at and current_updated_at and expected_updated_at != current_updated_at:
            raise AnalysisEditConflictError

        edited_account_blocks = set()
        for edit in edits:
            action = str(edit.get("action") or "update").strip().lower()
            if action not in {"update", "delete", "restore", "insert", "annotate"}:
                raise ValueError("Invalid edit action.")
            review_regions = None
            if "review_regions" in edit:
                if content.get("file_type") != "pdf":
                    raise ValueError("Marcações estão disponíveis apenas para PDFs.")
                review_regions = validate_review_regions(edit["review_regions"],
                    (content.get("pdf_processing_metrics") or {}).get("page_count"))

            if action == "insert":
                if statement_scopes(content.get("document_parts")) and not preview_rows:
                    raise InvalidFileContentError("A inserção exige um lançamento de referência que identifique a conta de origem.")
                amount = self._build_amount_from_credit_debit(edit.get("credit"), edit.get("debit"))
                description = str(edit.get("description") or "").strip()
                date = str(edit.get("date") or "").strip()
                if not description or not date:
                    raise ValueError("Date and description are required.")
                insert_position = self._resolve_insert_position(edit.get("insert_position"), max_index=len(preview_rows))
                new_row = TransactionRow(
                    date=date,
                    description=description,
                    amount=amount,
                    category="Outros",
                    reconciliation_status="unmatched",
                    is_deleted=False,
                    review_regions=review_regions or [],
                )
                if all(row.source_sequence is not None for row in preview_rows):
                    anchor_id = edit.get("anchor_transaction_id")
                    anchor = next((row for row in preview_rows if row.transaction_id == anchor_id), None) if anchor_id else None
                    if anchor_id and anchor is None:
                        raise ValueError("Anchor transaction not found.")
                    if any(row.statement_account for row in preview_rows):
                        if anchor is None or not anchor.statement_account:
                            raise InvalidFileContentError("Escolha um lançamento da conta de origem para inserir a nova linha.")
                        new_row.statement_account = dict(anchor.statement_account)
                        edited_account_blocks.add(anchor.statement_account["block_id"])
                    new_row.transaction_id = str(edit.get("transaction_id") or f"txn_{uuid4().hex}")
                    if any(row.transaction_id == new_row.transaction_id for row in preview_rows):
                        raise ValueError("Transaction ID already exists.")
                    new_row.source_sequence = anchor.source_sequence if anchor else 0
                    new_row.source_subsequence = 1 + max(
                        (row.source_subsequence for row in preview_rows if row.source_sequence == new_row.source_sequence), default=0,
                    )
                preview_rows.insert(insert_position, new_row)
                report_rows.insert(insert_position, TransactionRow(**asdict(new_row)))
                continue

            row_id_raw = edit.get("row_id")
            stable_index = next((idx for idx, row in enumerate(preview_rows)
                                 if row.transaction_id is not None and row.transaction_id == row_id_raw), None)
            row_index = stable_index if stable_index is not None else self._resolve_row_index(row_id_raw, max_index=len(preview_rows))

            if review_regions is not None:
                preview_rows[row_index].review_regions = list(review_regions)
                report_rows[row_index].review_regions = list(review_regions)
            if action == "annotate":
                if review_regions is None:
                    raise ValueError("Informe as marcações do lançamento.")
                if preview_rows[row_index].is_deleted:
                    raise ValueError("Restaure o lançamento antes de marcar.")
                continue

            if action == "delete":
                if preview_rows[row_index].statement_account:
                    edited_account_blocks.add(preview_rows[row_index].statement_account["block_id"])
                if row_index >= len(report_rows):
                    raise ValueError("row_id out of bounds.")
                preview_rows[row_index].is_deleted = True
                report_rows[row_index].is_deleted = True
                continue

            # Zero-valued entries can be present in imported statements. Editing
            # their text/date must preserve the amount when both cells are empty.
            preserve_zero = (
                preview_rows[row_index].amount == 0
                and edit.get("credit") is None
                and edit.get("debit") is None
            )
            amount = 0.0 if preserve_zero else self._build_amount_from_credit_debit(edit.get("credit"), edit.get("debit"))
            description = str(edit.get("description") or "").strip()
            date = str(edit.get("date") or "").strip()
            if not description or not date:
                raise ValueError("Date and description are required.")

            report_target = report_rows[row_index]
            if report_target.statement_account:
                edited_account_blocks.add(report_target.statement_account["block_id"])
            report_target.date = date
            report_target.description = description
            report_target.amount = amount
            report_target.is_deleted = False

            preview_target = preview_rows[row_index]
            preview_target.date = date
            preview_target.description = description
            preview_target.amount = amount
            preview_target.is_deleted = False

        if edits and all(str(edit.get("action") or "").strip().lower() == "annotate" for edit in edits) and all(value is None for value in (
                opening_balance, closing_balance, bank_branch, account_number, bank_code)):
            # Reviewer geometry must never recalculate balances or regenerate
            # financial artifacts. Versioning/receipts still protect concurrent edits.
            content["preview_transactions"] = [asdict(item) for item in preview_rows]
            content["report_transactions"] = [asdict(item) for item in report_rows]
            content["updated_at"] = self.now_provider().isoformat()
            if operation_id:
                receipts[operation_id] = {"hash": operation_hash, "updated_at": content["updated_at"]}
                content["edit_operations"] = dict(list(receipts.items())[-64:])
            return ReviewCommandResult()

        requested_overrides = self._normalize_convert_overrides(
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            bank_branch=bank_branch,
            account_number=account_number,
            bank_code=bank_code,
        )
        persisted_overrides = self._extract_convert_overrides(content)
        opening_balance_seed = self._resolve_recalculation_opening_balance(
            original_rows=report_rows_raw,
            current_rows=report_rows,
            content=content,
            requested_opening_balance=requested_overrides.get("opening_balance"),
            persisted_opening_balance=persisted_overrides.get("opening_balance"),
        )
        if any(row.statement_account for row in report_rows):
            opening_balance_seed = None
            if edited_account_blocks:
                refresh_edited_account_rows(preview_rows, report_rows, edited_account_blocks)
                content["pdf_processing_metrics"].update(financial_controls_validated=0, export_recommendation="review_recommended",
                    export_recommendation_reason="statement_account_edited", balance_validation_status="review_required")
        if opening_balance_seed is not None or not (content.get("pdf_processing_metrics") or {}).get("balance_bounds_unavailable"):
            self._recalculate_running_balances(
                preview_rows=preview_rows,
                report_rows=report_rows,
                opening_balance=opening_balance_seed,
            )

        active_rows = self._active_rows(report_rows)
        total_inflows = round(sum(item.amount for item in active_rows if item.amount > 0), 2)
        total_outflows = round(sum(item.amount for item in active_rows if item.amount < 0), 2)
        net_total = round(total_inflows + total_outflows, 2)
        effective_overrides = {
            "opening_balance": (
                requested_overrides.get("opening_balance")
                if requested_overrides.get("opening_balance") is not None
                else persisted_overrides.get("opening_balance")
            ),
            "closing_balance": (
                requested_overrides.get("closing_balance")
                if requested_overrides.get("closing_balance") is not None
                else persisted_overrides.get("closing_balance")
            ),
            "bank_branch": (
                requested_overrides.get("bank_branch")
                if requested_overrides.get("bank_branch") is not None
                else persisted_overrides.get("bank_branch")
            ),
            "account_number": (
                requested_overrides.get("account_number")
                if requested_overrides.get("account_number") is not None
                else persisted_overrides.get("account_number")
            ),
            "bank_code": (
                requested_overrides.get("bank_code")
                if requested_overrides.get("bank_code") is not None
                else persisted_overrides.get("bank_code")
            ),
        }
        if requested_overrides:
            self._apply_convert_overrides_to_content(content, requested_overrides)

        content["transactions_total"] = len(active_rows)
        content["total_inflows"] = total_inflows
        content["total_outflows"] = total_outflows
        content["net_total"] = net_total
        opening_balance_value, closing_balance_value = self._resolve_balance_bounds(
            active_rows,
            opening_balance_hint=effective_overrides.get("opening_balance"),
        )
        if (content.get("pdf_processing_metrics") or {}).get("balance_bounds_unavailable"):
            opening_balance_value = self._coerce_balance_value(effective_overrides.get("opening_balance"))
            closing_balance_value = self._coerce_balance_value(effective_overrides.get("closing_balance"))
        content["opening_balance"] = opening_balance_value
        content["closing_balance"] = closing_balance_value
        content["preview_transactions"] = [asdict(item) for item in preview_rows]
        content["report_transactions"] = [asdict(item) for item in report_rows]
        if "ocr_balance_arithmetic_matches" in (content.get("pdf_processing_metrics") or {}):
            content["pdf_processing_metrics"]["ocr_balance_arithmetic_matches"] = 0
        new_updated_at = self.now_provider().isoformat()
        content["updated_at"] = new_updated_at
        self._refresh_structured_result_content(content, preview_rows=preview_rows, report_rows=report_rows)

        if operation_id:
            receipts[operation_id] = {"hash": operation_hash, "updated_at": new_updated_at}
            content["edit_operations"] = dict(list(receipts.items())[-64:])
        return ReviewCommandResult(publish_artifacts=True, preview_rows=preview_rows, report_rows=report_rows,
                                   effective_overrides=effective_overrides)

    def prepare_export(
        self,
        content: dict[str, object],
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> ReviewCommandResult:
        report_rows_raw = content.get("report_transactions") or content.get("preview_transactions", [])
        report_rows = self._parse_transaction_rows(report_rows_raw)
        if not report_rows and not _is_confirmed_empty_statement(content):
            raise AnalysisNotFoundError

        assert_account_overrides(report_rows, closing_balance=closing_balance, bank_branch=bank_branch,
                                 account_number=account_number, bank_code=bank_code,
                                 statement_accounts=statement_scopes(content.get("document_parts")))

        persisted_overrides = self._extract_convert_overrides(content)
        requested_overrides = self._normalize_convert_overrides(
            opening_balance=None,
            closing_balance=closing_balance,
            bank_branch=bank_branch,
            account_number=account_number,
            bank_code=bank_code,
        )
        effective_overrides = {
            "opening_balance": persisted_overrides.get("opening_balance"),
            "closing_balance": (
                requested_overrides.get("closing_balance")
                if requested_overrides.get("closing_balance") is not None
                else persisted_overrides.get("closing_balance")
            ),
            "bank_branch": (
                requested_overrides.get("bank_branch")
                if requested_overrides.get("bank_branch") is not None
                else persisted_overrides.get("bank_branch")
            ),
            "account_number": (
                requested_overrides.get("account_number")
                if requested_overrides.get("account_number") is not None
                else persisted_overrides.get("account_number")
            ),
            "bank_code": (
                requested_overrides.get("bank_code")
                if requested_overrides.get("bank_code") is not None
                else persisted_overrides.get("bank_code")
            ),
        }
        metrics = content.get("pdf_processing_metrics") or {}
        if metrics.get("financial_controls_validated") and not metrics.get("balance_bounds_unavailable"):
            # Changing the destination account must preserve confirmed statement
            # boundaries. Explicit user overrides still take precedence.
            for key in ("opening_balance", "closing_balance"):
                if effective_overrides.get(key) is None:
                    effective_overrides[key] = content.get(key)
        if requested_overrides:
            self._apply_convert_overrides_to_content(content, requested_overrides)
            self._refresh_structured_result_content(content, preview_rows=report_rows, report_rows=report_rows)


        return ReviewCommandResult(publish_artifacts=True, report_rows=report_rows,
                                   effective_overrides=effective_overrides, metadata_changed=bool(requested_overrides))

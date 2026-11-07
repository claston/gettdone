"""Legacy analysis rules shared by review commands and artifact readers.

Extracted unchanged from TempAnalysisStorage; this module performs no I/O.
"""
from dataclasses import asdict

from app.application.balance_checks import parse_balance_check
from app.application.models import AnalysisData, BeforeAfterRow, TransactionRow
from app.application.source_regions import parse_source_regions
from app.application.structured_conversion import build_structured_conversion_result_from_analysis_data


def _is_confirmed_empty_statement(content: dict[str, object]) -> bool:
    metrics = content.get("pdf_processing_metrics")
    return (
        content.get("transactions_total") == 0
        and isinstance(metrics, dict)
        and metrics.get("statement_no_activity") == 1
        and content.get("preview_transactions") == []
        and content.get("report_transactions") == []
    )


class LegacyAnalysisState:
    def _normalize_convert_overrides(
        self,
        *,
        opening_balance: float | None,
        closing_balance: float | None,
        bank_branch: str | None,
        account_number: str | None,
        bank_code: str | None,
    ) -> dict[str, float | str]:
        normalized: dict[str, float | str] = {}
        if opening_balance is not None:
            normalized["opening_balance"] = float(opening_balance)
        if closing_balance is not None:
            normalized["closing_balance"] = float(closing_balance)
        branch = str(bank_branch or "").strip()
        if branch:
            normalized["bank_branch"] = branch
        account = str(account_number or "").strip()
        if account:
            normalized["account_number"] = account
        code = str(bank_code or "").strip()
        if code:
            normalized["bank_code"] = code
        return normalized


    def _extract_convert_overrides(self, content: dict[str, object]) -> dict[str, float | str | None]:
        empty_statement = _is_confirmed_empty_statement(content)
        opening_raw = content.get("convert_opening_balance")
        if opening_raw is None and empty_statement:
            opening_raw = content.get("opening_balance")
        opening_balance = float(opening_raw) if opening_raw is not None else None
        closing_raw = content.get("convert_closing_balance")
        if closing_raw is None and empty_statement:
            closing_raw = content.get("closing_balance")
        closing_balance = float(closing_raw) if closing_raw is not None else None
        preserve_bank_metadata = (content.get("file_type") == "ofx"
                                  or content.get("file_type") == "pdf" and content.get("financial_document") is None)
        source = content if preserve_bank_metadata or empty_statement else {}
        branch = str(content.get("convert_bank_branch") or source.get("bank_branch") or "").strip() or None
        account = str(content.get("convert_account_number") or source.get("account_number") or "").strip() or None
        code = str(content.get("convert_bank_code") or source.get("bank_code") or "").strip() or None
        return {
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "bank_branch": branch,
            "account_number": account,
            "bank_code": code,
        }


    def _apply_convert_overrides_to_content(
        self,
        content: dict[str, object],
        overrides: dict[str, float | str],
    ) -> None:
        if "opening_balance" in overrides:
            content["convert_opening_balance"] = float(overrides["opening_balance"])
        if "closing_balance" in overrides:
            content["convert_closing_balance"] = float(overrides["closing_balance"])
        if "bank_branch" in overrides:
            content["convert_bank_branch"] = str(overrides["bank_branch"])
        if "account_number" in overrides:
            content["convert_account_number"] = str(overrides["account_number"])
        if "bank_code" in overrides:
            content["convert_bank_code"] = str(overrides["bank_code"])


    def _parse_transaction_rows(self, rows_raw: object) -> list[TransactionRow]:
        parsed: list[TransactionRow] = []
        if not isinstance(rows_raw, list):
            return parsed
        for item in rows_raw:
            if not isinstance(item, dict):
                continue
            parsed.append(
                TransactionRow(
                    statement_account=dict(item["statement_account"]) if isinstance(item.get("statement_account"), dict) else None,
                    balance_check=parse_balance_check(item.get("balance_check")),
                    date=str(item.get("date") or ""),
                    description=str(item.get("description") or ""),
                    document_number=item.get("document_number") if isinstance(item.get("document_number"), str) else None,
                    document_reference_type=item.get("document_reference_type"),
                    amount=float(item.get("amount") or 0.0),
                    category=str(item.get("category") or "Outros"),
                    reconciliation_status=str(item.get("reconciliation_status") or "unmatched"),
                    source_page=item.get("source_page") if isinstance(item.get("source_page"), int) else None,
                    source_regions=parse_source_regions(item.get("source_regions")),
                    review_regions=parse_source_regions(item.get("review_regions")),
                    source_line=item.get("source_line") if isinstance(item.get("source_line"), int) else None,
                    transaction_id=item.get("transaction_id") if isinstance(item.get("transaction_id"), str) else None,
                    source_sequence=item.get("source_sequence") if isinstance(item.get("source_sequence"), int) else None,
                    source_subsequence=int(item.get("source_subsequence") or 0),
                    running_balance=(
                        float(item.get("running_balance"))
                        if item.get("running_balance") is not None
                        else None
                    ),
                    is_deleted=bool(item.get("is_deleted") or False),
                    warning_types=[
                        str(entry).strip()
                        for entry in (item.get("warning_types") or [])
                        if str(entry).strip()
                    ]
                    if isinstance(item.get("warning_types"), list)
                    else [],
                )
            )
        return parsed


    def _parse_before_after_rows(self, rows_raw: object) -> list[BeforeAfterRow]:
        parsed: list[BeforeAfterRow] = []
        if not isinstance(rows_raw, list):
            return parsed
        for item in rows_raw:
            if not isinstance(item, dict):
                continue
            parsed.append(
                BeforeAfterRow(
                    date=str(item.get("date") or ""),
                    description_before=str(item.get("description_before") or ""),
                    description_after=str(item.get("description_after") or ""),
                    amount_before=float(item.get("amount_before") or 0.0),
                    amount_after=float(item.get("amount_after") or 0.0),
                )
            )
        return parsed


    def _active_rows(self, rows: list[TransactionRow]) -> list[TransactionRow]:
        return [item for item in rows if not item.is_deleted]


    def _build_analysis_snapshot_from_content(
        self,
        content: dict[str, object],
        *,
        preview_rows: list[TransactionRow],
        report_rows: list[TransactionRow],
    ) -> AnalysisData:
        return AnalysisData(
            analysis_id=str(content.get("analysis_id", "")),
            file_type=str(content.get("file_type", "")),
            upload_filename=content.get("upload_filename") if isinstance(content.get("upload_filename"), str) else None,
            transactions_total=int(content.get("transactions_total", len(self._active_rows(report_rows)))),
            total_inflows=float(content.get("total_inflows", 0.0)),
            total_outflows=float(content.get("total_outflows", 0.0)),
            net_total=float(content.get("net_total", 0.0)),
            preview_transactions=preview_rows,
            report_transactions=report_rows,
            semantic_type=str(content.get("semantic_type") or "").strip() or None,
            semantic_confidence=(
                float(content["semantic_confidence"]) if content.get("semantic_confidence") is not None else None
            ),
            semantic_evidence=[
                str(item).strip()
                for item in (content.get("semantic_evidence") or [])
                if str(item).strip()
            ]
            if isinstance(content.get("semantic_evidence"), list)
            else None,
            document_type=str(content.get("document_type") or "").strip() or None,
            financial_document=content.get("financial_document"),
            document_type_confidence=(
                float(content["document_type_confidence"])
                if content.get("document_type_confidence") is not None
                else None
            ),
            document_type_evidence=[
                str(item).strip()
                for item in (content.get("document_type_evidence") or [])
                if str(item).strip()
            ]
            if isinstance(content.get("document_type_evidence"), list)
            else None,
            document_classification_version=(
                str(content.get("document_classification_version") or "").strip() or None
            ),
            document_parts=[dict(part) for part in content.get("document_parts", []) if isinstance(part, dict)]
            if isinstance(content.get("document_parts"), list) else [],
            preview_before_after=self._parse_before_after_rows(content.get("preview_before_after", [])),
            matched_groups=int(content.get("matched_groups", 0)),
            reversed_entries=int(content.get("reversed_entries", 0)),
            potential_duplicates=int(content.get("potential_duplicates", 0)),
            updated_at=str(content.get("updated_at") or "").strip() or None,
            layout_inference_name=str(content.get("layout_inference_name") or "").strip() or None,
            layout_inference_confidence=(
                float(content["layout_inference_confidence"])
                if content.get("layout_inference_confidence") is not None
                else None
            ),
            pdf_processing_metrics=(
                dict(content["pdf_processing_metrics"])
                if isinstance(content.get("pdf_processing_metrics"), dict)
                else None
            ),
            quality_issues=[dict(item) for item in content.get("quality_issues", []) if isinstance(item, dict)]
            if isinstance(content.get("quality_issues"), list)
            else [],
            ofx_account_type=str(content.get("ofx_account_type") or "").strip() or None,
            opening_balance=float(content["opening_balance"]) if content.get("opening_balance") is not None else None,
            closing_balance=float(content["closing_balance"]) if content.get("closing_balance") is not None else None,
            statement_balance_check=parse_balance_check(content.get("statement_balance_check")),
            bank_name=str(content.get("bank_name") or "").strip() or None,
            bank_branch=str(content.get("bank_branch") or "").strip() or None,
            account_number=str(content.get("account_number") or "").strip() or None,
            bank_code=str(content.get("bank_code") or "").strip() or None,
        )


    def _refresh_structured_result_content(
        self,
        content: dict[str, object],
        *,
        preview_rows: list[TransactionRow],
        report_rows: list[TransactionRow],
    ) -> None:
        snapshot = self._build_analysis_snapshot_from_content(
            content,
            preview_rows=preview_rows,
            report_rows=report_rows,
        )
        content["structured_result"] = asdict(
            build_structured_conversion_result_from_analysis_data(
                snapshot,
                expires_at=str(content.get("expires_at") or "").strip() or None,
            )
        )


    def _resolve_row_index(self, row_id_raw: object, max_index: int) -> int:
        row_id = str(row_id_raw or "").strip()
        if not row_id.startswith("row_"):
            raise ValueError("Invalid row_id.")
        suffix = row_id.split("_", 1)[1]
        if not suffix.isdigit():
            raise ValueError("Invalid row_id.")
        idx = int(suffix) - 1
        if idx < 0 or idx >= max_index:
            raise ValueError("row_id out of bounds.")
        return idx


    def _resolve_insert_position(self, position_raw: object, max_index: int) -> int:
        if position_raw is None:
            return max_index
        position = int(position_raw)
        if position < 0 or position > max_index:
            raise ValueError("insert_position out of bounds.")
        return position


    def _build_amount_from_credit_debit(self, credit_raw: object, debit_raw: object) -> float:
        credit = None if credit_raw is None else float(credit_raw)
        debit = None if debit_raw is None else float(debit_raw)
        has_credit = credit is not None and credit > 0
        has_debit = debit is not None and debit > 0
        if has_credit == has_debit:
            raise ValueError("Provide only one of credit or debit.")
        if has_credit:
            return round(float(credit), 2)
        return -round(abs(float(debit)), 2)


    def _resolve_balance_bounds(
        self,
        rows: list[TransactionRow],
        *,
        opening_balance_hint: float | str | None = None,
        closing_balance_hint: float | str | None = None,
    ) -> tuple[float | None, float | None]:
        opening_balance = self._coerce_balance_value(opening_balance_hint)
        if opening_balance is None:
            for item in rows:
                if self._is_opening_balance_row(item):
                    opening_balance = round(float(item.amount), 2)
                    break
            if opening_balance is None:
                for item in rows:
                    if item.running_balance is None:
                        continue
                    opening_balance = round(float(item.running_balance) - float(item.amount), 2)
                    break

        manual_closing_balance = self._coerce_balance_value(closing_balance_hint)
        if manual_closing_balance is not None:
            return opening_balance, manual_closing_balance

        last_balance_index: int | None = None
        closing_balance: float | None = None
        for index, item in enumerate(rows):
            if item.running_balance is None:
                continue
            last_balance_index = index
            closing_balance = round(float(item.running_balance), 2)
        if closing_balance is not None and last_balance_index is not None:
            trailing_amount = sum(float(item.amount) for item in rows[last_balance_index + 1 :])
            return opening_balance, round(closing_balance + trailing_amount, 2)

        if opening_balance is not None:
            transaction_amounts = sum(float(item.amount) for item in rows if not self._is_opening_balance_row(item))
            return opening_balance, round(opening_balance + transaction_amounts, 2)

        return None, None


    def _resolve_recalculation_opening_balance(
        self,
        *,
        original_rows: list[dict[str, object]] | list[TransactionRow],
        current_rows: list[TransactionRow],
        content: dict[str, object],
        requested_opening_balance: float | str | None,
        persisted_opening_balance: float | str | None,
    ) -> float | None:
        for candidate in (
            requested_opening_balance,
            persisted_opening_balance,
            content.get("opening_balance"),
        ):
            coerced = self._coerce_balance_value(candidate)
            if coerced is not None:
                return coerced

        original_parsed_rows = (
            original_rows
            if original_rows and isinstance(original_rows[0], TransactionRow)
            else self._parse_transaction_rows(original_rows)
        )
        if (content.get("pdf_processing_metrics") or {}).get("balance_bounds_unavailable"):
            return None
        original_opening_balance, _ = self._resolve_balance_bounds(original_parsed_rows)
        if original_opening_balance is not None:
            return original_opening_balance

        current_opening_balance, _ = self._resolve_balance_bounds(current_rows)
        return current_opening_balance


    def _recalculate_running_balances(
        self,
        *,
        preview_rows: list[TransactionRow],
        report_rows: list[TransactionRow],
        opening_balance: float | None,
    ) -> None:
        has_balance_context = opening_balance is not None or any(
            row.running_balance is not None for row in report_rows
        )
        if not has_balance_context:
            return

        cursor = round(float(opening_balance), 2) if opening_balance is not None else None
        for preview_row, report_row in zip(preview_rows, report_rows, strict=False):
            if preview_row.is_deleted or report_row.is_deleted:
                preview_row.running_balance = None
                report_row.running_balance = None
                continue

            if self._is_opening_balance_row(report_row):
                cursor = round(float(report_row.amount), 2)
                preview_row.running_balance = cursor
                report_row.running_balance = cursor
                continue

            if cursor is None:
                preview_row.running_balance = None
                report_row.running_balance = None
                continue

            cursor = round(cursor + float(report_row.amount), 2)
            preview_row.running_balance = cursor
            report_row.running_balance = cursor


    def _coerce_balance_value(self, value: float | str | None) -> float | None:
        if value is None or value == "":
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None


    def _is_opening_balance_row(self, row: TransactionRow) -> bool:
        normalized = " ".join(str(row.description or "").strip().upper().split())
        return normalized in {"SALDO ANTERIOR", "SALDO INICIAL"}


    @staticmethod
    def _edit_result(analysis_id: str, content: dict, operation_id: str | None = None) -> dict[str, object]:
        return {
            "processing_id": analysis_id,
            "operation_id": operation_id,
            **{key: content.get(key) for key in (
                "transactions_total", "total_inflows", "total_outflows", "net_total",
                "preview_transactions", "updated_at", "opening_balance", "closing_balance",
                "statement_balance_check",
            )},
        }

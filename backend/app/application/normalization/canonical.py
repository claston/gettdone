from typing import Protocol

from app.application.models import CanonicalTransaction, NormalizedTransaction


class ParsedTransactionRow(Protocol):
    transaction: NormalizedTransaction
    source_page: int | None
    source_line: int | None
    running_balance: float | None
    external_reference_id: str | None


def from_normalized_transaction(
    transaction: NormalizedTransaction,
    *,
    bank_name: str | None = None,
    layout_name: str | None = None,
    source_page: int | None = None,
    source_line: int | None = None,
    source_parser: str | None = None,
    running_balance: float | None = None,
    document_id: str | None = None,
    external_reference_id: str | None = None,
    warnings: list[str] | None = None,
    confidence: float | None = None,
) -> CanonicalTransaction:
    return CanonicalTransaction(
        date=transaction.date,
        description=transaction.description,
        amount=transaction.amount,
        type=transaction.type,
        bank_name=bank_name,
        layout_name=layout_name,
        source_page=source_page,
        source_line=source_line,
        source_parser=source_parser,
        running_balance=running_balance,
        document_id=document_id,
        external_reference_id=external_reference_id,
        warnings=list(warnings or []),
        confidence=confidence,
    )


def build_canonical_transactions(
    rows: list[ParsedTransactionRow],
    *,
    bank_name: str | None,
    layout_name: str,
    layout_used_fallback: bool,
    layout_confidence: float,
    source_parser: str,
) -> list[CanonicalTransaction]:
    warning_list = ["layout_fallback"] if layout_used_fallback else None
    return [
        from_normalized_transaction(
            row.transaction,
            bank_name=bank_name,
            layout_name=layout_name,
            source_page=row.source_page,
            source_line=row.source_line,
            source_parser=source_parser,
            running_balance=row.running_balance,
            external_reference_id=row.external_reference_id,
            warnings=warning_list,
            confidence=layout_confidence,
        )
        for row in rows
    ]

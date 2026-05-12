from app.application.models import CanonicalTransaction, NormalizedTransaction


def from_normalized_transaction(
    transaction: NormalizedTransaction,
    *,
    bank_name: str | None = None,
    layout_name: str | None = None,
    source_page: int | None = None,
    source_line: int | None = None,
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
        running_balance=running_balance,
        document_id=document_id,
        external_reference_id=external_reference_id,
        warnings=list(warnings or []),
        confidence=confidence,
    )

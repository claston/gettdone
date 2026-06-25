from dataclasses import dataclass
from typing import Protocol

from app.application.models import CanonicalTransaction, NormalizedTransaction


class ParsedDocumentWithCanonicalRows(Protocol):
    transactions: list[NormalizedTransaction]
    canonical_transactions: list[CanonicalTransaction] | None


@dataclass(frozen=True)
class TransactionMetadata:
    warning_types: list[list[str]]
    running_balances: list[float | None]


def build_transaction_metadata(parsed_document: ParsedDocumentWithCanonicalRows) -> TransactionMetadata:
    canonical_rows = list(parsed_document.canonical_transactions or [])
    warning_types = [list(item.warnings or []) for item in canonical_rows]
    running_balances = [item.running_balance for item in canonical_rows]
    transactions_count = len(parsed_document.transactions)

    if len(warning_types) < transactions_count:
        warning_types.extend([[] for _ in range(transactions_count - len(warning_types))])
    if len(running_balances) < transactions_count:
        running_balances.extend([None for _ in range(transactions_count - len(running_balances))])

    return TransactionMetadata(
        warning_types=warning_types[:transactions_count],
        running_balances=running_balances[:transactions_count],
    )

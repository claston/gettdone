import unicodedata
from dataclasses import dataclass
from datetime import datetime

from app.application.models import NormalizedTransaction

TRANSFER_KEYWORDS = ("pix", "transferencia", "transfer", "ted", "doc")
REVERSAL_KEYWORDS = ("estorno", "reversao", "reversal", "cancelamento", "chargeback")


@dataclass
class ReconciliationResult:
    statuses: list[str]
    matched_groups: int
    reversed_entries: int
    potential_duplicates: int = 0


def reconcile_transactions(transactions: list[NormalizedTransaction]) -> ReconciliationResult:
    statuses = ["unmatched"] * len(transactions)
    used_indexes: set[int] = set()
    matched_groups = 0
    reversed_entries = 0

    for i in range(len(transactions)):
        if i in used_indexes:
            continue
        for j in range(i + 1, len(transactions)):
            if j in used_indexes:
                continue
            if not _is_opposite_amount(transactions[i].amount, transactions[j].amount):
                continue
            if _days_between(transactions[i].date, transactions[j].date) > 7:
                continue
            if _is_reversal_pair(transactions[i], transactions[j]):
                statuses[i] = "reversed"
                statuses[j] = "reversed"
                used_indexes.add(i)
                used_indexes.add(j)
                reversed_entries += 2
                break

    for i in range(len(transactions)):
        if i in used_indexes:
            continue
        for j in range(i + 1, len(transactions)):
            if j in used_indexes:
                continue
            if not _is_opposite_amount(transactions[i].amount, transactions[j].amount):
                continue
            if _days_between(transactions[i].date, transactions[j].date) > 2:
                continue
            if _is_transfer_pair(transactions[i], transactions[j]):
                statuses[i] = "matched_transfer"
                statuses[j] = "matched_transfer"
                used_indexes.add(i)
                used_indexes.add(j)
                matched_groups += 1
                break

    return ReconciliationResult(
        statuses=statuses,
        matched_groups=matched_groups,
        reversed_entries=reversed_entries,
        potential_duplicates=0,
    )


def _is_reversal_pair(left: NormalizedTransaction, right: NormalizedTransaction) -> bool:
    left_text = _normalize_text(left.description)
    right_text = _normalize_text(right.description)
    return any(keyword in left_text or keyword in right_text for keyword in REVERSAL_KEYWORDS)


def _is_transfer_pair(left: NormalizedTransaction, right: NormalizedTransaction) -> bool:
    left_text = _normalize_text(left.description)
    right_text = _normalize_text(right.description)
    return any(keyword in left_text for keyword in TRANSFER_KEYWORDS) and any(
        keyword in right_text for keyword in TRANSFER_KEYWORDS
    )


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _is_opposite_amount(left: float, right: float) -> bool:
    return round(left + right, 2) == 0


def _days_between(left_date: str, right_date: str) -> int:
    left = datetime.strptime(left_date, "%Y-%m-%d")
    right = datetime.strptime(right_date, "%Y-%m-%d")
    return abs((left - right).days)

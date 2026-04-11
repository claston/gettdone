from datetime import date
from difflib import SequenceMatcher

from app.application.ledger_match_models import (
    LedgerClassificationResult,
    LedgerMatchResult,
    LedgerReconciliationRow,
)
from app.application.models import NormalizedTransaction

_DIVERGENCE_DATE_TOLERANCE_DAYS = 2
_DIVERGENCE_DESCRIPTION_SIMILARITY_THRESHOLD = 0.80


def classify_reconciliation_rows(
    bank_rows: list[NormalizedTransaction],
    sheet_rows: list[NormalizedTransaction],
    match_result: LedgerMatchResult,
) -> LedgerClassificationResult:
    rows: list[LedgerReconciliationRow] = []

    bank_row_ids = [_row_id("bank", idx) for idx in range(len(bank_rows))]
    sheet_row_ids = [_row_id("sheet", idx) for idx in range(len(sheet_rows))]

    matched_bank_indexes = {match.bank_index for match in match_result.matches}
    matched_sheet_indexes = {match.sheet_index for match in match_result.matches}

    for match in match_result.matches:
        bank = bank_rows[match.bank_index]
        sheet = sheet_rows[match.sheet_index]
        bank_id = bank_row_ids[match.bank_index]
        sheet_id = sheet_row_ids[match.sheet_index]
        rows.append(
            LedgerReconciliationRow(
                row_id=bank_id,
                source="bank",
                date=bank.date,
                description=bank.description,
                amount=bank.amount,
                status="conciliado",
                match_rule=match.match_rule,
                matched_row_id=sheet_id,
                reason=match.reason,
            )
        )
        rows.append(
            LedgerReconciliationRow(
                row_id=sheet_id,
                source="sheet",
                date=sheet.date,
                description=sheet.description,
                amount=sheet.amount,
                status="conciliado",
                match_rule=match.match_rule,
                matched_row_id=bank_id,
                reason=match.reason,
            )
        )

    unmatched_bank_indexes = [idx for idx in range(len(bank_rows)) if idx not in matched_bank_indexes]
    unmatched_sheet_indexes = [idx for idx in range(len(sheet_rows)) if idx not in matched_sheet_indexes]

    divergent_pairs = _pair_divergent_amount_mismatch(
        bank_rows=bank_rows,
        sheet_rows=sheet_rows,
        unmatched_bank_indexes=unmatched_bank_indexes,
        unmatched_sheet_indexes=unmatched_sheet_indexes,
    )

    divergent_bank_indexes = {bank_idx for bank_idx, _ in divergent_pairs}
    divergent_sheet_indexes = {sheet_idx for _, sheet_idx in divergent_pairs}

    for bank_idx, sheet_idx in divergent_pairs:
        bank = bank_rows[bank_idx]
        sheet = sheet_rows[sheet_idx]
        bank_id = bank_row_ids[bank_idx]
        sheet_id = sheet_row_ids[sheet_idx]
        rows.append(
            LedgerReconciliationRow(
                row_id=bank_id,
                source="bank",
                date=bank.date,
                description=bank.description,
                amount=bank.amount,
                status="divergente",
                match_rule="none",
                matched_row_id=sheet_id,
                reason="amount_mismatch",
            )
        )
        rows.append(
            LedgerReconciliationRow(
                row_id=sheet_id,
                source="sheet",
                date=sheet.date,
                description=sheet.description,
                amount=sheet.amount,
                status="divergente",
                match_rule="none",
                matched_row_id=bank_id,
                reason="amount_mismatch",
            )
        )

    for bank_idx in unmatched_bank_indexes:
        if bank_idx in divergent_bank_indexes:
            continue
        bank = bank_rows[bank_idx]
        rows.append(
            LedgerReconciliationRow(
                row_id=bank_row_ids[bank_idx],
                source="bank",
                date=bank.date,
                description=bank.description,
                amount=bank.amount,
                status="pendente",
                match_rule="none",
                matched_row_id=None,
                reason="missing_in_sheet",
            )
        )

    for sheet_idx in unmatched_sheet_indexes:
        if sheet_idx in divergent_sheet_indexes:
            continue
        sheet = sheet_rows[sheet_idx]
        rows.append(
            LedgerReconciliationRow(
                row_id=sheet_row_ids[sheet_idx],
                source="sheet",
                date=sheet.date,
                description=sheet.description,
                amount=sheet.amount,
                status="pendente",
                match_rule="none",
                matched_row_id=None,
                reason="missing_in_bank",
            )
        )

    conciliated_count = sum(1 for row in rows if row.status == "conciliado")
    pending_count = sum(1 for row in rows if row.status == "pendente")
    divergent_count = sum(1 for row in rows if row.status == "divergente")

    return LedgerClassificationResult(
        rows=rows,
        conciliated_count=conciliated_count,
        pending_count=pending_count,
        divergent_count=divergent_count,
    )


def _pair_divergent_amount_mismatch(
    bank_rows: list[NormalizedTransaction],
    sheet_rows: list[NormalizedTransaction],
    unmatched_bank_indexes: list[int],
    unmatched_sheet_indexes: list[int],
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    used_sheet_indexes: set[int] = set()

    for bank_idx in unmatched_bank_indexes:
        bank = bank_rows[bank_idx]
        bank_date = _parse_iso_date(bank.date)
        if bank_date is None:
            continue

        best_sheet_idx: int | None = None
        best_similarity: float = 0.0

        for sheet_idx in unmatched_sheet_indexes:
            if sheet_idx in used_sheet_indexes:
                continue

            sheet = sheet_rows[sheet_idx]
            if round(bank.amount, 2) == round(sheet.amount, 2):
                continue

            sheet_date = _parse_iso_date(sheet.date)
            if sheet_date is None:
                continue

            day_distance = abs((bank_date - sheet_date).days)
            if day_distance > _DIVERGENCE_DATE_TOLERANCE_DAYS:
                continue

            similarity = _description_similarity(bank.description, sheet.description)
            if similarity < _DIVERGENCE_DESCRIPTION_SIMILARITY_THRESHOLD:
                continue

            if similarity > best_similarity:
                best_similarity = similarity
                best_sheet_idx = sheet_idx

        if best_sheet_idx is not None:
            used_sheet_indexes.add(best_sheet_idx)
            pairs.append((bank_idx, best_sheet_idx))

    return pairs


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _description_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _row_id(source: str, index: int) -> str:
    return f"{source}_{index + 1:03d}"

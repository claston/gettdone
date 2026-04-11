import re
from datetime import date
from difflib import SequenceMatcher

from app.application.ledger_match_models import Ledger1to1Match, LedgerMatchResult
from app.application.models import NormalizedTransaction

_DATE_TOLERANCE_DAYS = 2
_DESCRIPTION_SIMILARITY_THRESHOLD = 0.70


def match_exact_then_date_tolerance_then_description_similarity_1to1(
    bank_rows: list[NormalizedTransaction],
    sheet_rows: list[NormalizedTransaction],
) -> LedgerMatchResult:
    matches: list[Ledger1to1Match] = []
    used_bank_indexes: set[int] = set()
    used_sheet_indexes: set[int] = set()

    # 1) exact (same date + same amount)
    for bank_index, bank_row in enumerate(bank_rows):
        for sheet_index, sheet_row in enumerate(sheet_rows):
            if bank_index in used_bank_indexes:
                continue
            if sheet_index in used_sheet_indexes:
                continue
            if bank_row.date != sheet_row.date:
                continue
            if round(bank_row.amount, 2) != round(sheet_row.amount, 2):
                continue

            matches.append(
                Ledger1to1Match(
                    bank_index=bank_index,
                    sheet_index=sheet_index,
                    date=bank_row.date,
                    amount=round(bank_row.amount, 2),
                    match_rule="exact",
                    reason="matched_exact_value_and_date",
                )
            )
            used_bank_indexes.add(bank_index)
            used_sheet_indexes.add(sheet_index)
            break

    # 2) date_tolerance (same amount + date within +/-2 days)
    for bank_index, bank_row in enumerate(bank_rows):
        if bank_index in used_bank_indexes:
            continue

        bank_date = _parse_iso_date(bank_row.date)
        if bank_date is None:
            continue

        bank_amount = round(bank_row.amount, 2)
        best_sheet_index: int | None = None
        best_day_distance: int | None = None

        for sheet_index, sheet_row in enumerate(sheet_rows):
            if sheet_index in used_sheet_indexes:
                continue
            if bank_amount != round(sheet_row.amount, 2):
                continue

            sheet_date = _parse_iso_date(sheet_row.date)
            if sheet_date is None:
                continue

            day_distance = abs((bank_date - sheet_date).days)
            if day_distance > _DATE_TOLERANCE_DAYS:
                continue

            if best_day_distance is None or day_distance < best_day_distance:
                best_sheet_index = sheet_index
                best_day_distance = day_distance

        if best_sheet_index is None:
            continue

        matches.append(
            Ledger1to1Match(
                bank_index=bank_index,
                sheet_index=best_sheet_index,
                date=bank_row.date,
                amount=bank_amount,
                match_rule="date_tolerance",
                reason="matched_equal_amount_within_2_days",
            )
        )
        used_bank_indexes.add(bank_index)
        used_sheet_indexes.add(best_sheet_index)

    # 3) description_similarity (same amount + similar description)
    for bank_index, bank_row in enumerate(bank_rows):
        if bank_index in used_bank_indexes:
            continue

        bank_amount = round(bank_row.amount, 2)
        best_sheet_index: int | None = None
        best_similarity: float = 0.0

        for sheet_index, sheet_row in enumerate(sheet_rows):
            if sheet_index in used_sheet_indexes:
                continue
            if bank_amount != round(sheet_row.amount, 2):
                continue

            similarity = _description_similarity_score(
                bank_description=bank_row.description,
                sheet_description=sheet_row.description,
            )
            if similarity < _DESCRIPTION_SIMILARITY_THRESHOLD:
                continue
            if similarity > best_similarity:
                best_similarity = similarity
                best_sheet_index = sheet_index

        if best_sheet_index is None:
            continue

        matches.append(
            Ledger1to1Match(
                bank_index=bank_index,
                sheet_index=best_sheet_index,
                date=bank_row.date,
                amount=bank_amount,
                match_rule="description_similarity",
                reason="matched_equal_amount_with_similar_description",
            )
        )
        used_bank_indexes.add(bank_index)
        used_sheet_indexes.add(best_sheet_index)

    exact_matches_count = sum(1 for match in matches if match.match_rule == "exact")
    date_tolerance_matches_count = sum(1 for match in matches if match.match_rule == "date_tolerance")
    description_similarity_matches_count = sum(
        1 for match in matches if match.match_rule == "description_similarity"
    )
    total_matches_count = exact_matches_count + date_tolerance_matches_count + description_similarity_matches_count
    bank_unmatched_count = len(bank_rows) - total_matches_count
    sheet_unmatched_count = len(sheet_rows) - total_matches_count

    return LedgerMatchResult(
        matches=matches,
        exact_matches_count=exact_matches_count,
        date_tolerance_matches_count=date_tolerance_matches_count,
        description_similarity_matches_count=description_similarity_matches_count,
        total_matches_count=total_matches_count,
        bank_unmatched_count=bank_unmatched_count,
        sheet_unmatched_count=sheet_unmatched_count,
    )


def _parse_iso_date(raw_value: str) -> date | None:
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


def _description_similarity_score(bank_description: str, sheet_description: str) -> float:
    left = _normalize_description(bank_description)
    right = _normalize_description(sheet_description)
    if not left or not right:
        return 0.0

    sequence_score = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split(" "))
    right_tokens = set(right.split(" "))
    common_tokens = left_tokens.intersection(right_tokens)
    common_count = len(common_tokens)
    token_overlap_score = common_count / max(len(left_tokens), len(right_tokens))
    token_containment_score = 0.0
    if common_count >= 2:
        token_containment_score = common_count / min(len(left_tokens), len(right_tokens))

    token_score = (token_overlap_score + token_containment_score) / 2
    return (sequence_score + token_score) / 2


def _normalize_description(value: str) -> str:
    only_alnum_or_space = re.sub(r"[^A-Z0-9 ]+", " ", value.upper())
    collapsed_spaces = re.sub(r"\s+", " ", only_alnum_or_space).strip()
    return collapsed_spaces

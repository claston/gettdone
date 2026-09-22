from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from app.application.ai_recovery.models import TransactionDirection

COMPARATOR_VERSION = "statement_comparator_v1"
MIN_PAIR_SCORE = Decimal("0.5000")
UNIQUE_MATCH_MARGIN = Decimal("0.1000")
_CENT = Decimal("0.01")
_SCORE_QUANTUM = Decimal("0.0001")
_BALANCE_ROW_LABELS = ("saldo anterior", "saldo inicial")
_REPEATED_HEADER_TOKENS = frozenset({"data", "historico", "descricao", "valor", "saldo"})


class RecoveryDiagnosisCode(str, Enum):
    DETERMINISTIC_MISSING_TRANSACTION = "deterministic_missing_transaction"
    DETERMINISTIC_DUPLICATE_TRANSACTION = "deterministic_duplicate_transaction"
    SIGN_INVERTED = "sign_inverted"
    RUNNING_BALANCE_USED_AS_AMOUNT = "running_balance_used_as_amount"
    OPENING_BALANCE_USED_AS_TRANSACTION = "opening_balance_used_as_transaction"
    COLUMN_SHIFTED = "column_shifted"
    MULTILINE_DESCRIPTION_SPLIT = "multiline_description_split"
    MULTILINE_DESCRIPTION_MERGED = "multiline_description_merged"
    DATE_ASSOCIATED_TO_WRONG_ROW = "date_associated_to_wrong_row"
    TRANSACTION_ORDER_INVERTED = "transaction_order_inverted"
    REPEATED_HEADER_USED_AS_DATA = "repeated_header_used_as_data"
    RUNNING_BALANCE_MISMATCH = "running_balance_mismatch"
    DESCRIPTION_MISMATCH = "description_mismatch"
    EVIDENCE_MISMATCH = "evidence_mismatch"
    SOURCE_PAGE_MISMATCH = "source_page_mismatch"
    MISSING_DATE = "missing_date"
    UNKNOWN_DIRECTION = "unknown_direction"
    UNEXPLAINED_AI_EXTRA = "unexplained_ai_extra"
    UNEXPLAINED_DETERMINISTIC_EXTRA = "unexplained_deterministic_extra"
    AMBIGUOUS_ALIGNMENT = "ambiguous_alignment"


@dataclass(frozen=True, slots=True)
class ComparisonTransaction:
    date: date | None
    description_lines: tuple[str, ...]
    amount: Decimal
    direction: TransactionDirection
    running_balance: Decimal | None = None
    page: int | None = None
    visual_order: int | None = None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_money(self.amount, field_name="amount", non_negative=True)
        if self.running_balance is not None:
            _validate_money(self.running_balance, field_name="running_balance", non_negative=False)
        if not self.description_lines:
            raise ValueError("description_lines must not be empty.")
        normalized_lines = tuple(str(line or "").strip() for line in self.description_lines)
        if any(not line for line in normalized_lines):
            raise ValueError("description_lines must contain non-empty strings.")
        if self.page is not None and self.page < 1:
            raise ValueError("page must be positive when provided.")
        if self.visual_order is not None and self.visual_order < 1:
            raise ValueError("visual_order must be positive when provided.")
        normalized_evidence = tuple(dict.fromkeys(str(item or "").strip() for item in self.evidence_ids))
        if any(not item for item in normalized_evidence):
            raise ValueError("evidence_ids must contain non-empty strings.")
        object.__setattr__(self, "description_lines", normalized_lines)
        object.__setattr__(self, "evidence_ids", normalized_evidence)


@dataclass(frozen=True, slots=True)
class ComparisonLedger:
    transactions: tuple[ComparisonTransaction, ...]
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None

    def __post_init__(self) -> None:
        if self.opening_balance is not None:
            _validate_money(self.opening_balance, field_name="opening_balance", non_negative=False)
        if self.closing_balance is not None:
            _validate_money(self.closing_balance, field_name="closing_balance", non_negative=False)


@dataclass(frozen=True, slots=True)
class RecoveryRowMatch:
    deterministic_index: int
    nova_index: int
    score: Decimal
    unique: bool
    evidence_overlap: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryDiagnosis:
    code: RecoveryDiagnosisCode
    deterministic_indexes: tuple[int, ...] = ()
    nova_indexes: tuple[int, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryComparisonResult:
    comparator_version: str
    matches: tuple[RecoveryRowMatch, ...]
    deterministic_unmatched: tuple[int, ...]
    nova_unmatched: tuple[int, ...]
    diagnoses: tuple[RecoveryDiagnosis, ...]
    exact: bool
    financially_equivalent: bool
    evidence_coverage: Decimal

    @property
    def diagnosis_codes(self) -> tuple[RecoveryDiagnosisCode, ...]:
        return tuple(dict.fromkeys(diagnosis.code for diagnosis in self.diagnoses))


def compare_recovery_ledgers(
    *,
    deterministic: ComparisonLedger,
    nova: ComparisonLedger,
) -> RecoveryComparisonResult:
    pairs, deterministic_unmatched, nova_unmatched = _align_sequences(
        deterministic.transactions,
        nova.transactions,
    )
    matches = tuple(
        _build_match(
            deterministic_index=deterministic_index,
            nova_index=nova_index,
            deterministic=deterministic.transactions,
            nova=nova.transactions,
        )
        for deterministic_index, nova_index in pairs
    )

    diagnoses: list[RecoveryDiagnosis] = []
    if _has_order_inversion(deterministic.transactions, nova.transactions):
        diagnoses.append(
            RecoveryDiagnosis(
                RecoveryDiagnosisCode.TRANSACTION_ORDER_INVERTED,
                deterministic_indexes=tuple(range(len(deterministic.transactions))),
                nova_indexes=tuple(range(len(nova.transactions))),
            )
        )

    for match in matches:
        deterministic_row = deterministic.transactions[match.deterministic_index]
        nova_row = nova.transactions[match.nova_index]
        if not match.unique:
            diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.AMBIGUOUS_ALIGNMENT, match))
        diagnoses.extend(_diagnose_matched_pair(match, deterministic_row, nova_row))

    matched_deterministic = {match.deterministic_index for match in matches}
    for index in deterministic_unmatched:
        diagnoses.append(
            _diagnose_unmatched_deterministic(
                index=index,
                ledger=deterministic,
                matched_indexes=matched_deterministic,
            )
        )

    for index in nova_unmatched:
        row = nova.transactions[index]
        if _is_ambiguous_nova_alternative(index=index, row=row, matches=matches, deterministic=deterministic.transactions):
            code = RecoveryDiagnosisCode.UNEXPLAINED_AI_EXTRA
        elif row.evidence_ids:
            code = RecoveryDiagnosisCode.DETERMINISTIC_MISSING_TRANSACTION
        else:
            code = RecoveryDiagnosisCode.UNEXPLAINED_AI_EXTRA
        diagnoses.append(
            RecoveryDiagnosis(
                code,
                nova_indexes=(index,),
                evidence_ids=row.evidence_ids,
            )
        )

    deduplicated_diagnoses = _deduplicate_diagnoses(diagnoses)
    no_unmatched = not deterministic_unmatched and not nova_unmatched
    balances_match = (
        deterministic.opening_balance == nova.opening_balance
        and deterministic.closing_balance == nova.closing_balance
    )
    financially_equivalent = no_unmatched and balances_match and all(
        _financial_fields_equal(
            deterministic.transactions[match.deterministic_index],
            nova.transactions[match.nova_index],
        )
        for match in matches
    )
    exact = financially_equivalent and not deduplicated_diagnoses and all(
        _transactions_exact(
            deterministic.transactions[match.deterministic_index],
            nova.transactions[match.nova_index],
        )
        for match in matches
    )
    evidence_coverage = _evidence_coverage(nova.transactions)
    return RecoveryComparisonResult(
        comparator_version=COMPARATOR_VERSION,
        matches=matches,
        deterministic_unmatched=deterministic_unmatched,
        nova_unmatched=nova_unmatched,
        diagnoses=deduplicated_diagnoses,
        exact=exact,
        financially_equivalent=financially_equivalent,
        evidence_coverage=evidence_coverage,
    )


def _align_sequences(
    deterministic: tuple[ComparisonTransaction, ...],
    nova: tuple[ComparisonTransaction, ...],
) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...], tuple[int, ...]]:
    rows = len(deterministic)
    columns = len(nova)
    scores = [[Decimal("0") for _ in range(columns + 1)] for _ in range(rows + 1)]
    operations = [["" for _ in range(columns + 1)] for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        operations[row][0] = "skip_deterministic"
    for column in range(1, columns + 1):
        operations[0][column] = "skip_nova"

    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            candidates: list[tuple[Decimal, int, str]] = [
                (scores[row - 1][column], 3, "skip_deterministic"),
                (scores[row][column - 1], 2, "skip_nova"),
            ]
            pair_score = _pair_score(deterministic[row - 1], nova[column - 1])
            if pair_score >= MIN_PAIR_SCORE:
                candidates.append((scores[row - 1][column - 1] + pair_score, 1, "match"))
            best_score, _priority, operation = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))
            scores[row][column] = best_score
            operations[row][column] = operation

    pairs: list[tuple[int, int]] = []
    deterministic_unmatched: list[int] = []
    nova_unmatched: list[int] = []
    row = rows
    column = columns
    while row > 0 or column > 0:
        operation = operations[row][column]
        if operation == "match":
            pairs.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif operation == "skip_deterministic":
            deterministic_unmatched.append(row - 1)
            row -= 1
        else:
            nova_unmatched.append(column - 1)
            column -= 1

    return (
        tuple(reversed(pairs)),
        tuple(reversed(deterministic_unmatched)),
        tuple(reversed(nova_unmatched)),
    )


def _build_match(
    *,
    deterministic_index: int,
    nova_index: int,
    deterministic: tuple[ComparisonTransaction, ...],
    nova: tuple[ComparisonTransaction, ...],
) -> RecoveryRowMatch:
    deterministic_row = deterministic[deterministic_index]
    nova_row = nova[nova_index]
    score = _pair_score(deterministic_row, nova_row)
    alternatives = [
        _pair_score(deterministic_row, candidate)
        for index, candidate in enumerate(nova)
        if index != nova_index
    ]
    alternatives.extend(
        _pair_score(candidate, nova_row)
        for index, candidate in enumerate(deterministic)
        if index != deterministic_index
    )
    best_alternative = max(alternatives, default=Decimal("0"))
    evidence_overlap = tuple(sorted(set(deterministic_row.evidence_ids).intersection(nova_row.evidence_ids)))
    unique_evidence_anchor = bool(evidence_overlap) and not _has_competing_evidence_anchor(
        deterministic_index=deterministic_index,
        nova_index=nova_index,
        deterministic=deterministic,
        nova=nova,
    )
    unique = unique_evidence_anchor or score - best_alternative >= UNIQUE_MATCH_MARGIN
    return RecoveryRowMatch(
        deterministic_index=deterministic_index,
        nova_index=nova_index,
        score=score,
        unique=unique,
        evidence_overlap=evidence_overlap,
    )


def _has_competing_evidence_anchor(
    *,
    deterministic_index: int,
    nova_index: int,
    deterministic: tuple[ComparisonTransaction, ...],
    nova: tuple[ComparisonTransaction, ...],
) -> bool:
    deterministic_evidence = set(deterministic[deterministic_index].evidence_ids)
    nova_evidence = set(nova[nova_index].evidence_ids)
    for index, candidate in enumerate(nova):
        if index != nova_index and deterministic_evidence.intersection(candidate.evidence_ids):
            return True
    for index, candidate in enumerate(deterministic):
        if index != deterministic_index and nova_evidence.intersection(candidate.evidence_ids):
            return True
    return False


def _pair_score(deterministic: ComparisonTransaction, nova: ComparisonTransaction) -> Decimal:
    score = Decimal("0")
    if _money_equal(deterministic.amount, nova.amount):
        score += Decimal("0.35")
    if deterministic.date is not None and deterministic.date == nova.date:
        score += Decimal("0.20")
    if deterministic.direction == nova.direction and deterministic.direction != TransactionDirection.UNKNOWN:
        score += Decimal("0.15")
    score += Decimal("0.15") * _description_similarity(deterministic, nova)
    if (
        deterministic.running_balance is not None
        and nova.running_balance is not None
        and _money_equal(deterministic.running_balance, nova.running_balance)
    ):
        score += Decimal("0.10")
    if set(deterministic.evidence_ids).intersection(nova.evidence_ids):
        score += Decimal("0.05")
    elif deterministic.page is not None and deterministic.page == nova.page:
        score += Decimal("0.02")
    return score.quantize(_SCORE_QUANTUM)


def _diagnose_matched_pair(
    match: RecoveryRowMatch,
    deterministic: ComparisonTransaction,
    nova: ComparisonTransaction,
) -> list[RecoveryDiagnosis]:
    diagnoses: list[RecoveryDiagnosis] = []
    if deterministic.date is None or nova.date is None:
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.MISSING_DATE, match))
    if (
        deterministic.direction == TransactionDirection.UNKNOWN
        or nova.direction == TransactionDirection.UNKNOWN
    ):
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.UNKNOWN_DIRECTION, match))
    financial_identity_without_direction = (
        deterministic.date == nova.date
        and _money_equal(deterministic.amount, nova.amount)
        and _description_similarity(deterministic, nova) >= Decimal("0.70")
    )
    if deterministic.direction != nova.direction and financial_identity_without_direction:
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.SIGN_INVERTED, match))

    if not _money_equal(deterministic.amount, nova.amount):
        if nova.running_balance is not None and _money_equal(deterministic.amount, abs(nova.running_balance)):
            code = RecoveryDiagnosisCode.RUNNING_BALANCE_USED_AS_AMOUNT
        else:
            code = RecoveryDiagnosisCode.COLUMN_SHIFTED
        diagnoses.append(_diagnosis_for_match(code, match))

    if deterministic.date != nova.date:
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.DATE_ASSOCIATED_TO_WRONG_ROW, match))

    if deterministic.description_lines != nova.description_lines:
        deterministic_tokens = _description_tokens(deterministic)
        nova_tokens = _description_tokens(nova)
        if (
            _financial_fields_equal(deterministic, nova)
            and len(nova.description_lines) > len(deterministic.description_lines)
            and deterministic_tokens <= nova_tokens
        ):
            code = RecoveryDiagnosisCode.MULTILINE_DESCRIPTION_SPLIT
        elif (
            _financial_fields_equal(deterministic, nova)
            and len(deterministic.description_lines) > len(nova.description_lines)
            and nova_tokens <= deterministic_tokens
        ):
            code = RecoveryDiagnosisCode.MULTILINE_DESCRIPTION_MERGED
        else:
            code = RecoveryDiagnosisCode.DESCRIPTION_MISMATCH
        diagnoses.append(_diagnosis_for_match(code, match))

    if (
        deterministic.running_balance is not None
        and nova.running_balance is not None
        and not _money_equal(deterministic.running_balance, nova.running_balance)
    ):
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.RUNNING_BALANCE_MISMATCH, match))
    if set(deterministic.evidence_ids) != set(nova.evidence_ids):
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.EVIDENCE_MISMATCH, match))
    if deterministic.page != nova.page:
        diagnoses.append(_diagnosis_for_match(RecoveryDiagnosisCode.SOURCE_PAGE_MISMATCH, match))
    return diagnoses


def _diagnose_unmatched_deterministic(
    *,
    index: int,
    ledger: ComparisonLedger,
    matched_indexes: set[int],
) -> RecoveryDiagnosis:
    row = ledger.transactions[index]
    if _is_deterministic_duplicate(index=index, transactions=ledger.transactions, matched_indexes=matched_indexes):
        code = RecoveryDiagnosisCode.DETERMINISTIC_DUPLICATE_TRANSACTION
    elif _is_opening_balance_row(row, opening_balance=ledger.opening_balance):
        code = RecoveryDiagnosisCode.OPENING_BALANCE_USED_AS_TRANSACTION
    elif _is_repeated_header_row(row):
        code = RecoveryDiagnosisCode.REPEATED_HEADER_USED_AS_DATA
    else:
        code = RecoveryDiagnosisCode.UNEXPLAINED_DETERMINISTIC_EXTRA
    return RecoveryDiagnosis(code, deterministic_indexes=(index,), evidence_ids=row.evidence_ids)


def _diagnosis_for_match(code: RecoveryDiagnosisCode, match: RecoveryRowMatch) -> RecoveryDiagnosis:
    return RecoveryDiagnosis(
        code,
        deterministic_indexes=(match.deterministic_index,),
        nova_indexes=(match.nova_index,),
        evidence_ids=match.evidence_overlap,
    )


def _is_ambiguous_nova_alternative(
    *,
    index: int,
    row: ComparisonTransaction,
    matches: tuple[RecoveryRowMatch, ...],
    deterministic: tuple[ComparisonTransaction, ...],
) -> bool:
    for match in matches:
        if match.unique:
            continue
        alternative_score = _pair_score(deterministic[match.deterministic_index], row)
        if match.score - alternative_score < UNIQUE_MATCH_MARGIN:
            return True
    return False


def _is_deterministic_duplicate(
    *,
    index: int,
    transactions: tuple[ComparisonTransaction, ...],
    matched_indexes: set[int],
) -> bool:
    row = transactions[index]
    for candidate_index in matched_indexes:
        candidate = transactions[candidate_index]
        same_fingerprint = _transaction_fingerprint(row) == _transaction_fingerprint(candidate)
        shared_evidence = bool(set(row.evidence_ids).intersection(candidate.evidence_ids))
        if same_fingerprint and (shared_evidence or abs(candidate_index - index) == 1):
            return True
    return False


def _is_opening_balance_row(row: ComparisonTransaction, *, opening_balance: Decimal | None) -> bool:
    if opening_balance is None or not _money_equal(row.amount, abs(opening_balance)):
        return False
    normalized = _normalize_text(" ".join(row.description_lines))
    return any(normalized == label or normalized.startswith(f"{label} ") for label in _BALANCE_ROW_LABELS)


def _is_repeated_header_row(row: ComparisonTransaction) -> bool:
    tokens = _description_tokens(row)
    return len(tokens.intersection(_REPEATED_HEADER_TOKENS)) >= 3


def _has_order_inversion(
    deterministic: tuple[ComparisonTransaction, ...],
    nova: tuple[ComparisonTransaction, ...],
) -> bool:
    if len(deterministic) < 2 or len(deterministic) != len(nova):
        return False
    deterministic_fingerprints = [_transaction_fingerprint(row) for row in deterministic]
    nova_fingerprints = [_transaction_fingerprint(row) for row in nova]
    if len(set(deterministic_fingerprints)) != len(deterministic_fingerprints):
        return False
    if set(deterministic_fingerprints) != set(nova_fingerprints):
        return False
    nova_positions = {fingerprint: index for index, fingerprint in enumerate(nova_fingerprints)}
    mapped_positions = [nova_positions[fingerprint] for fingerprint in deterministic_fingerprints]
    return mapped_positions != sorted(mapped_positions)


def _financial_fields_equal(first: ComparisonTransaction, second: ComparisonTransaction) -> bool:
    return all(
        (
            first.date == second.date,
            _money_equal(first.amount, second.amount),
            first.direction == second.direction,
            _optional_money_equal(first.running_balance, second.running_balance),
        )
    )


def _transactions_exact(first: ComparisonTransaction, second: ComparisonTransaction) -> bool:
    return all(
        (
            _financial_fields_equal(first, second),
            first.description_lines == second.description_lines,
            first.page == second.page,
            set(first.evidence_ids) == set(second.evidence_ids),
        )
    )


def _transaction_fingerprint(transaction: ComparisonTransaction) -> tuple[object, ...]:
    return (
        transaction.date,
        transaction.amount.quantize(_CENT),
        transaction.direction,
        _normalize_text(" ".join(transaction.description_lines)),
    )


def _description_similarity(first: ComparisonTransaction, second: ComparisonTransaction) -> Decimal:
    first_tokens = _description_tokens(first)
    second_tokens = _description_tokens(second)
    if not first_tokens or not second_tokens:
        return Decimal("0")
    return (Decimal(len(first_tokens.intersection(second_tokens))) / Decimal(len(first_tokens.union(second_tokens)))).quantize(
        _SCORE_QUANTUM
    )


def _description_tokens(transaction: ComparisonTransaction) -> frozenset[str]:
    return frozenset(_normalize_text(" ".join(transaction.description_lines)).split())


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.casefold()).strip()


def _evidence_coverage(transactions: tuple[ComparisonTransaction, ...]) -> Decimal:
    if not transactions:
        return Decimal("0").quantize(_SCORE_QUANTUM)
    evidenced = sum(bool(transaction.evidence_ids) for transaction in transactions)
    return (Decimal(evidenced) / Decimal(len(transactions))).quantize(_SCORE_QUANTUM)


def _deduplicate_diagnoses(diagnoses: list[RecoveryDiagnosis]) -> tuple[RecoveryDiagnosis, ...]:
    unique: dict[tuple[object, ...], RecoveryDiagnosis] = {}
    for diagnosis in diagnoses:
        key = (diagnosis.code, diagnosis.deterministic_indexes, diagnosis.nova_indexes)
        unique.setdefault(key, diagnosis)
    return tuple(unique.values())


def _money_equal(first: Decimal, second: Decimal) -> bool:
    return first.quantize(_CENT) == second.quantize(_CENT)


def _optional_money_equal(first: Decimal | None, second: Decimal | None) -> bool:
    if first is None or second is None:
        return first is second
    return _money_equal(first, second)


def _validate_money(value: object, *, field_name: str, non_negative: bool) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal, not binary float or integer.")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    if non_negative and value < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    if value != value.quantize(_CENT):
        raise ValueError(f"{field_name} must have at most two decimal places.")

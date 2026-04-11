from dataclasses import dataclass


@dataclass
class Ledger1to1Match:
    bank_index: int
    sheet_index: int
    date: str
    amount: float
    match_rule: str
    reason: str


@dataclass
class LedgerMatchResult:
    matches: list[Ledger1to1Match]
    exact_matches_count: int
    date_tolerance_matches_count: int
    description_similarity_matches_count: int
    total_matches_count: int
    bank_unmatched_count: int
    sheet_unmatched_count: int


@dataclass
class LedgerReconciliationRow:
    row_id: str
    source: str
    date: str
    description: str
    amount: float
    status: str
    match_rule: str
    matched_row_id: str | None
    reason: str


@dataclass
class LedgerClassificationResult:
    rows: list[LedgerReconciliationRow]
    conciliated_count: int
    pending_count: int
    divergent_count: int


@dataclass
class LedgerProblemInsight:
    type: str
    title: str
    description: str

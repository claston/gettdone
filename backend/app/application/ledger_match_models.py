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
    bank_unmatched_count: int
    sheet_unmatched_count: int

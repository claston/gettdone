from dataclasses import dataclass, field


@dataclass
class TransactionRow:
    date: str
    description: str
    amount: float
    category: str
    reconciliation_status: str
    is_deleted: bool = False


@dataclass
class NormalizedTransaction:
    date: str
    description: str
    amount: float
    type: str


@dataclass
class BeforeAfterRow:
    date: str
    description_before: str
    description_after: str
    amount_before: float
    amount_after: float


@dataclass
class AnalysisData:
    analysis_id: str
    file_type: str
    upload_filename: str | None
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    preview_transactions: list[TransactionRow]
    report_transactions: list[TransactionRow] | None = None
    preview_before_after: list[BeforeAfterRow] = field(default_factory=list)
    matched_groups: int = 0
    reversed_entries: int = 0
    potential_duplicates: int = 0
    updated_at: str | None = None

from dataclasses import dataclass, field


@dataclass
class TransactionRow:
    date: str
    description: str
    amount: float
    category: str
    reconciliation_status: str
    running_balance: float | None = None
    is_deleted: bool = False
    warning_types: list[str] = field(default_factory=list)


@dataclass
class NormalizedTransaction:
    date: str
    description: str
    amount: float
    type: str


@dataclass
class CanonicalTransaction:
    date: str
    description: str
    amount: float
    type: str
    bank_name: str | None = None
    layout_name: str | None = None
    source_page: int | None = None
    source_line: int | None = None
    source_parser: str | None = None
    running_balance: float | None = None
    document_id: str | None = None
    external_reference_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None


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
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None
    pdf_processing_metrics: dict[str, int | float | str] | None = None
    ofx_account_type: str | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    bank_branch: str | None = None
    account_number: str | None = None

from pydantic import BaseModel


class ReconciliationSummary(BaseModel):
    matched_groups: int
    reversed_entries: int
    potential_duplicates: int


class OperationalSummary(BaseModel):
    total_volume: float
    inflow_count: int
    outflow_count: int
    reconciled_entries: int
    unmatched_entries: int


class CategorySummary(BaseModel):
    category: str
    total: float
    count: int


class TopExpense(BaseModel):
    description: str
    amount: float
    date: str
    category: str


class Insight(BaseModel):
    type: str
    title: str
    description: str


class TransactionPreview(BaseModel):
    date: str
    description: str
    amount: float
    running_balance: float | None = None
    category: str
    reconciliation_status: str
    is_deleted: bool = False
    warning_types: list[str] = []


class BeforeAfterPreview(BaseModel):
    date: str
    description_before: str
    description_after: str
    amount_before: float
    amount_after: float


class PdfProcessingMetrics(BaseModel):
    total_ms: float
    parse_ms: float
    classify_ms: float
    normalize_ms: float
    reconcile_ms: float
    page_count: int
    extracted_char_count: int
    flattened_line_count: int
    grouped_transactions_count: int
    inline_candidates_count: int
    inline_transactions_count: int
    tabular_candidates_count: int = 0
    tabular_transactions_count: int = 0
    columnar_candidates_count: int = 0
    columnar_transactions_count: int = 0
    multiline_candidates_count: int = 0
    multiline_transactions_count: int = 0
    multiline_overlap_count: int = 0
    multiline_coverage_gain: int = 0
    multiline_conflict_count: int = 0
    selected_parser: str
    parser_selection_reason: str = ""
    extraction_provider: str = ""
    textract_used: int = 0
    textract_enabled: int = 0
    textract_attempted: int = 0
    textract_error_type: str = ""
    native_text_detected: int = 0
    inline_decision: str = ""
    tabular_decision: str = ""
    columnar_decision: str = ""
    multiline_decision: str = ""
    confidence_band: str = ""
    export_recommendation: str = ""
    export_recommendation_reason: str = ""
    balance_consistency_checked: int = 0
    balance_consistency_failed: int = 0
    canonical_transactions_count: int = 0
    canonical_with_running_balance_count: int = 0
    canonical_with_external_reference_count: int = 0
    canonical_warning_count: int = 0
    canonical_balance_warning_count: int = 0
    canonical_warning_transactions_count: int = 0
    canonical_warning_types_count: int = 0
    canonical_warning_types: str = ""
    canonical_warning_types_list: list[str] = []
    canonical_running_balance_coverage_rate: float = 0.0
    canonical_external_reference_coverage_rate: float = 0.0
    canonical_warning_transaction_rate: float = 0.0
    canonical_source_parser_grouped_count: int = 0
    canonical_source_parser_inline_count: int = 0
    canonical_source_parser_tabular_count: int = 0
    canonical_source_parser_columnar_count: int = 0
    canonical_source_parser_multiline_count: int = 0
    canonical_source_parser_types_count: int = 0
    canonical_source_parser_types: str = ""
    canonical_source_parser_types_list: list[str] = []


class AnalyzeResponse(BaseModel):
    analysis_id: str
    file_type: str
    semantic_type: str | None = None
    semantic_confidence: float | None = None
    semantic_evidence: list[str] | None = None
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    operational_summary: OperationalSummary
    reconciliation: ReconciliationSummary
    categories: list[CategorySummary]
    top_expenses: list[TopExpense]
    insights: list[Insight]
    preview_transactions: list[TransactionPreview]
    preview_before_after: list[BeforeAfterPreview]
    expires_at: str | None
    updated_at: str | None = None
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None
    pdf_processing_metrics: PdfProcessingMetrics | None = None
    ofx_account_type: str | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    bank_name: str | None = None
    bank_branch: str | None = None
    account_number: str | None = None
    bank_code: str | None = None


class ConvertResponse(BaseModel):
    processing_id: str
    quota_remaining: int
    quota_limit: int
    quota_mode: str = "conversion"
    identity_type: str
    analysis: AnalyzeResponse

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
    category: str
    reconciliation_status: str


class BeforeAfterPreview(BaseModel):
    date: str
    description_before: str
    description_after: str
    amount_before: float
    amount_after: float


class AnalyzeResponse(BaseModel):
    analysis_id: str
    file_type: str
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


class ReconcileIntakeResponse(BaseModel):
    status: str
    bank_filename: str
    bank_file_type: str
    sheet_filename: str
    sheet_file_type: str
    sheet_rows_parsed: int
    sheet_mapping_detected: dict[str, str]

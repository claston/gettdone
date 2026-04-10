from pydantic import BaseModel


class ReconciliationSummary(BaseModel):
    matched_groups: int
    reversed_entries: int
    potential_duplicates: int


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


class AnalyzeResponse(BaseModel):
    analysis_id: str
    file_type: str
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    reconciliation: ReconciliationSummary
    categories: list[CategorySummary]
    top_expenses: list[TopExpense]
    insights: list[Insight]
    preview_transactions: list[TransactionPreview]
    expires_at: str | None


from dataclasses import dataclass


@dataclass
class TransactionRow:
    date: str
    description: str
    amount: float
    category: str
    reconciliation_status: str


@dataclass
class AnalysisData:
    analysis_id: str
    file_type: str
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    preview_transactions: list[TransactionRow]


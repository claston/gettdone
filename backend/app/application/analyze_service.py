from pathlib import Path
from uuid import uuid4

from app.application.errors import UnsupportedFileTypeError
from app.application.models import AnalysisData, TransactionRow
from app.application.storage_service import TempAnalysisStorage
from app.schemas import (
    AnalyzeResponse,
    CategorySummary,
    Insight,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)

SUPPORTED_EXTENSIONS = {"csv", "xlsx", "ofx"}


class AnalyzeService:
    def __init__(self, storage: TempAnalysisStorage) -> None:
        self.storage = storage

    def analyze(self, filename: str, raw_bytes: bytes) -> AnalyzeResponse:
        extension = Path(filename).suffix.replace(".", "").lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError

        analysis_id = f"an_{uuid4().hex[:12]}"
        byte_size = max(len(raw_bytes), 1)
        debit = round(-(byte_size % 500) - 120.5, 2)
        credit = round((byte_size % 900) + 350.75, 2)
        net = round(credit + debit, 2)

        preview_rows = [
            TransactionRow(
                date="2026-04-01",
                description="IFOOD SAO PAULO",
                amount=-58.90,
                category="Alimentacao",
                reconciliation_status="unmatched",
            ),
            TransactionRow(
                date="2026-04-02",
                description="PIX TRANSFERENCIA",
                amount=-350.75,
                category="Transferencias",
                reconciliation_status="matched",
            ),
            TransactionRow(
                date="2026-04-02",
                description="PIX RECEBIDO",
                amount=350.75,
                category="Transferencias",
                reconciliation_status="matched",
            ),
        ]

        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type=extension,
            transactions_total=len(preview_rows),
            total_inflows=credit,
            total_outflows=debit,
            net_total=net,
            preview_transactions=preview_rows,
        )
        self.storage.save_analysis(analysis_data)

        return AnalyzeResponse(
            analysis_id=analysis_id,
            file_type=extension,
            transactions_total=analysis_data.transactions_total,
            total_inflows=analysis_data.total_inflows,
            total_outflows=analysis_data.total_outflows,
            net_total=analysis_data.net_total,
            reconciliation=ReconciliationSummary(
                matched_groups=1,
                reversed_entries=0,
                potential_duplicates=0,
            ),
            categories=[
                CategorySummary(category="Transferencias", total=0.0, count=2),
                CategorySummary(category="Alimentacao", total=-58.90, count=1),
            ],
            top_expenses=[
                TopExpense(
                    description="PIX TRANSFERENCIA",
                    amount=-350.75,
                    date="2026-04-02",
                    category="Transferencias",
                ),
                TopExpense(
                    description="IFOOD SAO PAULO",
                    amount=-58.90,
                    date="2026-04-01",
                    category="Alimentacao",
                ),
            ],
            insights=[
                Insight(
                    type="foundation_mode",
                    title="Fundacao ativa",
                    description="Pipeline base criada. Proxima etapa: parser real para CSV/XLSX/OFX.",
                )
            ],
            preview_transactions=[
                TransactionPreview(
                    date=row.date,
                    description=row.description,
                    amount=row.amount,
                    category=row.category,
                    reconciliation_status=row.reconciliation_status,
                )
                for row in preview_rows
            ],
            expires_at=None,
        )


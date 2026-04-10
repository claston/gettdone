from pathlib import Path
from uuid import uuid4

from app.application.csv_parser import parse_csv_transactions
from app.application.errors import UnsupportedFileTypeError
from app.application.models import AnalysisData, NormalizedTransaction, TransactionRow
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
        transactions = self._build_transactions_for_extension(extension, raw_bytes)
        preview_rows = [
            TransactionRow(
                date=item.date,
                description=item.description,
                amount=item.amount,
                category="Outros",
                reconciliation_status="unmatched",
            )
            for item in transactions[:20]
        ]

        total_inflows = round(sum(item.amount for item in transactions if item.amount > 0), 2)
        total_outflows = round(sum(item.amount for item in transactions if item.amount < 0), 2)
        net_total = round(total_inflows + total_outflows, 2)
        top_expenses_rows = sorted((item for item in transactions if item.amount < 0), key=lambda x: x.amount)[:10]

        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type=extension,
            transactions_total=len(transactions),
            total_inflows=total_inflows,
            total_outflows=total_outflows,
            net_total=net_total,
            preview_transactions=preview_rows,
        )
        expires_at = self.storage.save_analysis(analysis_data)

        return AnalyzeResponse(
            analysis_id=analysis_id,
            file_type=extension,
            transactions_total=analysis_data.transactions_total,
            total_inflows=analysis_data.total_inflows,
            total_outflows=analysis_data.total_outflows,
            net_total=analysis_data.net_total,
            reconciliation=ReconciliationSummary(
                matched_groups=0,
                reversed_entries=0,
                potential_duplicates=0,
            ),
            categories=[CategorySummary(category="Outros", total=net_total, count=len(transactions))],
            top_expenses=[
                TopExpense(
                    description=row.description,
                    amount=row.amount,
                    date=row.date,
                    category="Outros",
                )
                for row in top_expenses_rows
            ],
            insights=[
                Insight(
                    type="csv_real_parser" if extension == "csv" else "foundation_mode",
                    title="CSV processado" if extension == "csv" else "Fundacao ativa",
                    description=(
                        "Extrato CSV processado com parser real e normalizacao inicial."
                        if extension == "csv"
                        else "Pipeline base criada. Proxima etapa: parser real para XLSX/OFX."
                    ),
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
            expires_at=expires_at,
        )

    def _build_transactions_for_extension(self, extension: str, raw_bytes: bytes) -> list[NormalizedTransaction]:
        if extension == "csv":
            return parse_csv_transactions(raw_bytes)

        byte_size = max(len(raw_bytes), 1)
        debit = round(-(byte_size % 500) - 120.5, 2)
        credit = round((byte_size % 900) + 350.75, 2)
        return [
            NormalizedTransaction(
                date="2026-04-01",
                description="IFOOD SAO PAULO",
                amount=-58.90,
                type="outflow",
            ),
            NormalizedTransaction(
                date="2026-04-02",
                description="PIX TRANSFERENCIA",
                amount=debit,
                type="outflow",
            ),
            NormalizedTransaction(
                date="2026-04-02",
                description="PIX RECEBIDO",
                amount=credit,
                type="inflow",
            ),
        ]

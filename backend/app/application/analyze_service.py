from pathlib import Path
from uuid import uuid4

from app.application.csv_parser import parse_csv_transactions
from app.application.errors import UnsupportedFileTypeError
from app.application.models import AnalysisData, BeforeAfterRow, NormalizedTransaction, TransactionRow
from app.application.normalizer import normalize_transactions
from app.application.ofx_parser import parse_ofx_transactions
from app.application.pdf_parser import parse_pdf_transactions
from app.application.reconciliation import reconcile_transactions
from app.application.storage_service import TempAnalysisStorage
from app.application.xlsx_parser import parse_xlsx_transactions
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)

SUPPORTED_EXTENSIONS = {"csv", "xlsx", "ofx", "pdf"}


class AnalyzeService:
    def __init__(self, storage: TempAnalysisStorage) -> None:
        self.storage = storage

    def analyze(self, filename: str, raw_bytes: bytes) -> AnalyzeResponse:
        extension = Path(filename).suffix.replace(".", "").lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError

        analysis_id = f"an_{uuid4().hex[:12]}"
        parsed_transactions, layout_inference_name, layout_inference_confidence = self._build_transactions_for_extension(
            extension,
            raw_bytes,
        )
        transactions = normalize_transactions(parsed_transactions)
        reconciliation_result = reconcile_transactions(transactions)
        preview_before_after = [
            BeforeAfterRow(
                date=after_item.date,
                description_before=before_item.description,
                description_after=after_item.description,
                amount_before=before_item.amount,
                amount_after=after_item.amount,
            )
            for before_item, after_item in zip(parsed_transactions[:20], transactions[:20], strict=False)
        ]
        preview_rows = [
            TransactionRow(
                date=item.date,
                description=item.description,
                amount=item.amount,
                category="Outros",
                reconciliation_status=reconciliation_result.statuses[idx],
            )
            for idx, item in enumerate(transactions[:20])
        ]
        report_rows = [
            TransactionRow(
                date=item.date,
                description=item.description,
                amount=item.amount,
                category="Outros",
                reconciliation_status=reconciliation_result.statuses[idx],
            )
            for idx, item in enumerate(transactions)
        ]

        total_inflows = round(sum(item.amount for item in transactions if item.amount > 0), 2)
        total_outflows = round(sum(item.amount for item in transactions if item.amount < 0), 2)
        net_total = round(total_inflows + total_outflows, 2)
        total_volume = round(sum(abs(item.amount) for item in transactions), 2)
        inflow_count = sum(1 for item in transactions if item.amount > 0)
        outflow_count = sum(1 for item in transactions if item.amount < 0)
        reconciled_entries = sum(1 for status in reconciliation_result.statuses if status != "unmatched")
        unmatched_entries = len(transactions) - reconciled_entries
        top_expenses_rows = sorted((item for item in transactions if item.amount < 0), key=lambda x: x.amount)[:10]

        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type=extension,
            transactions_total=len(transactions),
            total_inflows=total_inflows,
            total_outflows=total_outflows,
            net_total=net_total,
            preview_transactions=preview_rows,
            report_transactions=report_rows,
            preview_before_after=preview_before_after,
            matched_groups=reconciliation_result.matched_groups,
            reversed_entries=reconciliation_result.reversed_entries,
            potential_duplicates=reconciliation_result.potential_duplicates,
        )
        expires_at = self.storage.save_analysis(analysis_data)

        return AnalyzeResponse(
            analysis_id=analysis_id,
            file_type=extension,
            transactions_total=analysis_data.transactions_total,
            total_inflows=analysis_data.total_inflows,
            total_outflows=analysis_data.total_outflows,
            net_total=analysis_data.net_total,
            operational_summary=OperationalSummary(
                total_volume=total_volume,
                inflow_count=inflow_count,
                outflow_count=outflow_count,
                reconciled_entries=reconciled_entries,
                unmatched_entries=unmatched_entries,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=analysis_data.matched_groups,
                reversed_entries=analysis_data.reversed_entries,
                potential_duplicates=analysis_data.potential_duplicates,
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
                    type=f"{extension}_real_parser",
                    title=f"{extension.upper()} processado",
                    description=f"Extrato {extension.upper()} processado com parser real e normalizacao inicial.",
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
            preview_before_after=[
                BeforeAfterPreview(
                    date=row.date,
                    description_before=row.description_before,
                    description_after=row.description_after,
                    amount_before=row.amount_before,
                    amount_after=row.amount_after,
                )
                for row in preview_before_after
            ],
            expires_at=expires_at,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
        )

    def _build_transactions_for_extension(
        self,
        extension: str,
        raw_bytes: bytes,
    ) -> tuple[list[NormalizedTransaction], str | None, float | None]:
        if extension == "csv":
            return parse_csv_transactions(raw_bytes), None, None
        if extension == "xlsx":
            return parse_xlsx_transactions(raw_bytes), None, None
        if extension == "ofx":
            return parse_ofx_transactions(raw_bytes), None, None
        result = parse_pdf_transactions(raw_bytes)
        return result.transactions, result.layout.layout_name, result.layout.confidence

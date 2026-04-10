import json
from dataclasses import asdict
from pathlib import Path

from openpyxl import Workbook

from app.application.errors import AnalysisNotFoundError
from app.application.models import AnalysisData


class TempAnalysisStorage:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_analysis(self, data: AnalysisData) -> None:
        analysis_dir = self.root_dir / data.analysis_id
        analysis_dir.mkdir(parents=True, exist_ok=True)

        json_path = analysis_dir / "analysis.json"
        json_path.write_text(
            json.dumps(
                {
                    **asdict(data),
                    "preview_transactions": [asdict(item) for item in data.preview_transactions],
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Transacoes"
        sheet.append(["date", "description", "amount", "category", "reconciliation_status"])
        for item in data.preview_transactions:
            sheet.append([item.date, item.description, item.amount, item.category, item.reconciliation_status])
        workbook.save(analysis_dir / "report.xlsx")

    def get_report_path(self, analysis_id: str) -> Path:
        report_path = self.root_dir / analysis_id / "report.xlsx"
        if not report_path.exists():
            raise AnalysisNotFoundError
        return report_path


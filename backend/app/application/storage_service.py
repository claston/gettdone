import csv
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Callable
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.application.errors import AnalysisNotFoundError
from app.application.models import AnalysisData, NormalizedTransaction, TransactionRow
from app.application.ofx_writer import build_ofx_statement


class TempAnalysisStorage:
    def __init__(
        self,
        root_dir: Path,
        ttl_seconds: int = 24 * 60 * 60,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.ttl_seconds = ttl_seconds
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_analysis(self, data: AnalysisData) -> str:
        analysis_dir = self.root_dir / data.analysis_id
        analysis_dir.mkdir(parents=True, exist_ok=True)
        now = self.now_provider()
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        report_rows = data.report_transactions or data.preview_transactions

        json_path = analysis_dir / "analysis.json"
        json_path.write_text(
            json.dumps(
                {
                    **asdict(data),
                    "created_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "preview_transactions": [asdict(item) for item in data.preview_transactions],
                    "report_transactions": [asdict(item) for item in report_rows],
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
        for item in report_rows:
            sheet.append([item.date, item.description, item.amount, item.category, item.reconciliation_status])
        self._format_transacoes_sheet(sheet)
        self._add_conciliacao_sheet(workbook, data)
        workbook.save(analysis_dir / "report.xlsx")
        self._write_convert_artifacts(analysis_dir, report_rows)
        return expires_at.isoformat()

    def get_report_path(self, analysis_id: str) -> Path:
        analysis_dir = self.root_dir / analysis_id
        report_path = analysis_dir / "report.xlsx"
        if not report_path.exists():
            raise AnalysisNotFoundError
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError
        return report_path

    def get_convert_report_path(self, analysis_id: str, file_format: str) -> Path:
        analysis_dir = self.root_dir / analysis_id
        suffix = "ofx" if file_format == "ofx" else "csv"
        report_path = analysis_dir / f"converted.{suffix}"
        if not report_path.exists():
            raise AnalysisNotFoundError
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError
        return report_path

    def get_upload_filename(self, analysis_id: str) -> str | None:
        analysis_dir = self.root_dir / analysis_id
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError

        metadata_path = analysis_dir / "analysis.json"
        if not metadata_path.exists():
            raise AnalysisNotFoundError
        try:
            content = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        raw_name = content.get("upload_filename")
        if not isinstance(raw_name, str):
            return None
        name = raw_name.strip()
        return name or None

    def save_reconcile_report(
        self,
        summary: dict[str, int],
        reconciliation_rows: list[dict[str, str | float | None]],
        problems: list[dict[str, str]],
    ) -> tuple[str, str]:
        analysis_id = self._build_analysis_id(prefix="rc")
        analysis_dir = self.root_dir / analysis_id
        analysis_dir.mkdir(parents=True, exist_ok=True)
        now = self.now_provider()
        expires_at = now + timedelta(seconds=self.ttl_seconds)

        json_path = analysis_dir / "reconcile.json"
        json_path.write_text(
            json.dumps(
                {
                    "analysis_id": analysis_id,
                    "created_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "summary": summary,
                    "reconciliation_rows": reconciliation_rows,
                    "problems": problems,
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        workbook = Workbook()
        summary_sheet = workbook.active
        summary_sheet.title = "Resumo"
        summary_sheet.append(["Metrica", "Valor"])
        summary_sheet.append(["Total linhas extrato", summary.get("total_bank_rows", 0)])
        summary_sheet.append(["Total linhas planilha", summary.get("total_sheet_rows", 0)])
        summary_sheet.append(["Conciliados", summary.get("conciliated_count", 0)])
        summary_sheet.append(["Pendentes", summary.get("pending_count", 0)])
        summary_sheet.append(["Divergentes", summary.get("divergent_count", 0)])
        self._format_transacoes_sheet(summary_sheet)

        detail_sheet = workbook.create_sheet(title="Conciliacao_Detalhada")
        detail_headers = [
            "Linha",
            "Fonte",
            "Data",
            "Descricao",
            "Valor",
            "Status",
            "Regra de match",
            "Motivo",
            "Linha pareada",
        ]
        detail_sheet.append(detail_headers)
        for row in reconciliation_rows:
            detail_sheet.append(
                [
                    row.get("row_id"),
                    self._translate_source_label(row.get("source")),
                    row.get("date"),
                    row.get("description"),
                    row.get("amount"),
                    self._translate_status_label(row.get("status")),
                    self._translate_match_rule_label(row.get("match_rule")),
                    self._translate_reason_label(row.get("reason")),
                    row.get("matched_row_id"),
                ]
            )
        self._format_transacoes_sheet(detail_sheet)

        problems_sheet = workbook.create_sheet(title="Problemas")
        problem_row_headers = [
            "Linha",
            "Fonte",
            "Data",
            "Descricao",
            "Valor",
            "Status",
            "Motivo",
            "Linha pareada",
        ]
        problems_sheet.append(problem_row_headers)
        problematic_rows = [
            row for row in reconciliation_rows if row.get("status") in {"pendente", "divergente"}
        ]
        for row in problematic_rows:
            problems_sheet.append(
                [
                    row.get("row_id"),
                    self._translate_source_label(row.get("source")),
                    row.get("date"),
                    row.get("description"),
                    row.get("amount"),
                    self._translate_status_label(row.get("status")),
                    self._translate_reason_label(row.get("reason")),
                    row.get("matched_row_id"),
                ]
            )
        if len(problematic_rows) == 0:
            problems_sheet.append(
                [
                    "nenhum",
                    "Sistema",
                    "",
                    "Nenhuma pendencia ou divergencia foi identificada.",
                    "",
                    "-",
                    "-",
                    "",
                ]
            )
        self._format_transacoes_sheet(problems_sheet)

        workbook.save(analysis_dir / "reconcile_report.xlsx")

        csv_headers = detail_headers
        csv_lines = [",".join(csv_headers)]
        for row in reconciliation_rows:
            values = [
                self._escape_csv_value(value)
                for value in [
                    row.get("row_id"),
                    self._translate_source_label(row.get("source")),
                    row.get("date"),
                    row.get("description"),
                    row.get("amount"),
                    self._translate_status_label(row.get("status")),
                    self._translate_match_rule_label(row.get("match_rule")),
                    self._translate_reason_label(row.get("reason")),
                    row.get("matched_row_id"),
                ]
            ]
            csv_lines.append(",".join(values))
        (analysis_dir / "reconcile_report.csv").write_text("\n".join(csv_lines), encoding="utf-8")

        return analysis_id, expires_at.isoformat()

    def get_reconcile_report_path(self, analysis_id: str, file_format: str) -> Path:
        analysis_dir = self.root_dir / analysis_id
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError

        suffix = "xlsx" if file_format == "xlsx" else "csv"
        report_path = analysis_dir / f"reconcile_report.{suffix}"
        if not report_path.exists():
            raise AnalysisNotFoundError
        return report_path

    def _write_convert_artifacts(self, analysis_dir: Path, report_rows: list[TransactionRow]) -> None:
        normalized_transactions = [
            NormalizedTransaction(
                date=item.date,
                description=item.description,
                amount=item.amount,
                type="inflow" if item.amount >= 0 else "outflow",
            )
            for item in report_rows
        ]

        (analysis_dir / "converted.ofx").write_text(
            build_ofx_statement(normalized_transactions),
            encoding="utf-8",
        )

        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["date", "description", "amount", "category", "reconciliation_status"])
        for item in report_rows:
            writer.writerow([item.date, item.description, item.amount, item.category, item.reconciliation_status])
        (analysis_dir / "converted.csv").write_text(csv_buffer.getvalue(), encoding="utf-8")

    def _is_expired(self, analysis_dir: Path) -> bool:
        metadata_path = analysis_dir / "analysis.json"
        try:
            content = json.loads(metadata_path.read_text(encoding="utf-8"))
            expires_at_raw = content.get("expires_at")
            if not isinstance(expires_at_raw, str):
                return False
            expires_at = datetime.fromisoformat(expires_at_raw)
            return self.now_provider() > expires_at
        except (OSError, TypeError, json.JSONDecodeError, ValueError):
            return False

    def _cleanup_analysis(self, analysis_dir: Path) -> None:
        if not analysis_dir.exists():
            return
        for file in analysis_dir.glob("**/*"):
            if file.is_file():
                file.unlink(missing_ok=True)
        for directory in sorted((item for item in analysis_dir.glob("**/*") if item.is_dir()), key=lambda x: len(x.parts), reverse=True):
            directory.rmdir()
        analysis_dir.rmdir()

    def _format_transacoes_sheet(self, sheet) -> None:
        if sheet.max_row >= 1 and sheet.max_column >= 1:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions

        for column_index in range(1, sheet.max_column + 1):
            column_letter = get_column_letter(column_index)
            max_len = 0
            for row_index in range(1, sheet.max_row + 1):
                cell_value = sheet.cell(row=row_index, column=column_index).value
                max_len = max(max_len, len(str(cell_value)) if cell_value is not None else 0)
            sheet.column_dimensions[column_letter].width = min(max(max_len + 2, 12), 80)

    def _add_conciliacao_sheet(self, workbook: Workbook, data: AnalysisData) -> None:
        sheet = workbook.create_sheet(title="Conciliacao")
        sheet.append(["metric", "value"])
        sheet.append(["matched_groups", data.matched_groups])
        sheet.append(["reversed_entries", data.reversed_entries])
        sheet.append(["potential_duplicates", data.potential_duplicates])
        sheet.append([])
        sheet.append(["date", "description", "amount", "category", "reconciliation_status"])
        for item in data.preview_transactions:
            if item.reconciliation_status != "unmatched":
                sheet.append([item.date, item.description, item.amount, item.category, item.reconciliation_status])

        if sheet.max_row == 6:
            sheet.append(["-", "No reconciled entries in preview", "", "", "unmatched"])

        self._format_transacoes_sheet(sheet)

    def _build_analysis_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"

    def _escape_csv_value(self, value: object) -> str:
        text = "" if value is None else str(value)
        if any(char in text for char in [",", "\"", "\n", "\r"]):
            return f"\"{text.replace('\"', '\"\"')}\""
        return text

    def _translate_source_label(self, source: object) -> str:
        text = "" if source is None else str(source)
        labels = {
            "bank": "Banco",
            "sheet": "Planilha",
            "system": "Sistema",
        }
        return labels.get(text, text or "-")

    def _translate_status_label(self, status: object) -> str:
        text = "" if status is None else str(status)
        labels = {
            "conciliado": "Conciliado",
            "pendente": "Pendente",
            "divergente": "Divergente",
            "none": "-",
        }
        return labels.get(text, text or "-")

    def _translate_match_rule_label(self, match_rule: object) -> str:
        text = "" if match_rule is None else str(match_rule)
        labels = {
            "exact": "Exato",
            "date_tolerance": "Tolerancia de data",
            "description_similarity": "Similaridade de descricao",
            "none": "-",
        }
        return labels.get(text, text or "-")

    def _translate_reason_label(self, reason: object) -> str:
        text = "" if reason is None else str(reason)
        labels = {
            "missing_in_sheet": "Pendente na planilha",
            "missing_in_bank": "Pendente no banco",
            "amount_mismatch": "Diferenca de valor",
            "date_out_of_tolerance_window": "Data fora da tolerancia",
            "matched_equal_amount_same_day": "Valor igual na mesma data",
            "matched_equal_amount_within_2_days": "Valor igual dentro de 2 dias",
            "matched_equal_amount_with_similar_description": "Valor igual com descricao similar",
            "none": "-",
        }
        return labels.get(text, text or "-")

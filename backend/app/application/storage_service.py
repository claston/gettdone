import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from app.application.analysis_lock import locked_analysis
from app.application.bank_identity import resolve_conversion_model_label
from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError, AnalysisNotFoundError
from app.application.export_artifact_service import ExportArtifactService
from app.application.financial_document_export import assert_financial_export
from app.application.legacy_analysis_state import LegacyAnalysisState
from app.application.models import AnalysisData, TransactionRow
from app.application.review_commands import ReviewCommandResult, ReviewCommandService
from app.application.statement_account_export import statement_scopes
from app.application.structured_conversion import build_structured_conversion_result_from_analysis_data

_SAFE_ANALYSIS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class TempAnalysisStorage(LegacyAnalysisState):
    def __init__(
        self,
        root_dir: Path,
        ttl_seconds: int = 24 * 60 * 60,
        now_provider: Callable[[], datetime] | None = None,
        export_artifact_service: ExportArtifactService | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.ttl_seconds = ttl_seconds
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.export_artifact_service = export_artifact_service or ExportArtifactService()
        self.review_commands = ReviewCommandService(now_provider=lambda: self.now_provider())
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_analysis(self, data: AnalysisData) -> str:
        analysis_dir = self._resolve_analysis_dir(data.analysis_id)
        if (analysis_dir / "analysis.json").exists():
            try:
                existing = json.loads((analysis_dir / "analysis.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            self._assert_legacy_authority(existing)
        analysis_dir.mkdir(parents=True, exist_ok=True)
        now = self.now_provider()
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        report_rows = data.report_transactions or data.preview_transactions
        updated_at = data.updated_at or now.isoformat()
        data.updated_at = updated_at
        data.structured_result = build_structured_conversion_result_from_analysis_data(
            data,
            expires_at=expires_at.isoformat(),
        )

        json_path = analysis_dir / "analysis.json"
        json_path.write_text(
            json.dumps(
                {
                    **asdict(data),
                    "created_at": now.isoformat(),
                    "updated_at": updated_at,
                    "expires_at": expires_at.isoformat(),
                    "preview_transactions": [asdict(item) for item in data.preview_transactions],
                    "report_transactions": [asdict(item) for item in report_rows],
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.export_artifact_service.write_analysis_report_workbook(
            analysis_dir / "report.xlsx",
            report_rows=report_rows,
            snapshot=data,
        )
        preserve_bank_metadata = data.file_type == "ofx" or (data.file_type == "pdf" and data.financial_document is None)
        self.export_artifact_service.write_convert_artifacts(
            analysis_dir,
            report_rows=report_rows,
            ofx_account_type=data.ofx_account_type,
            layout_inference_name=data.layout_inference_name,
            opening_balance=data.opening_balance,
            closing_balance=data.closing_balance,
            balance_bounds_unavailable=bool((data.pdf_processing_metrics or {}).get("balance_bounds_unavailable")),
            bank_branch=data.bank_branch if preserve_bank_metadata else None,
            account_number=data.account_number if preserve_bank_metadata else None,
            bank_code=data.bank_code if preserve_bank_metadata else None,
            allow_layout_bank_fallback=not (data.pdf_processing_metrics or {}).get("statement_issuer_source"),
            **({"financial_document": data.financial_document} if data.financial_document is not None else {}),
            **({"statement_accounts": statement_scopes(data.document_parts)} if statement_scopes(data.document_parts) else {}),
        )
        return expires_at.isoformat()

    def get_report_path(self, analysis_id: str) -> Path:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        report_path = analysis_dir / "report.xlsx"
        if not report_path.exists():
            raise AnalysisNotFoundError
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError
        return report_path

    def get_convert_report_path(
        self,
        analysis_id: str,
        file_format: str,
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Path:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        content, _ = self._read_analysis_metadata(analysis_id=analysis_id, metadata_filename="analysis.json")
        financial_document = content.get("financial_document")
        if financial_document is not None:
            persisted = self._extract_convert_overrides(content)
            assert_financial_export(financial_document, file_format,
                bank_code=bank_code or persisted.get("bank_code"), bank_branch=bank_branch or persisted.get("bank_branch"),
                account_number=account_number or persisted.get("account_number"))
        if file_format == "ofx" and (
            closing_balance is not None
            or (str(bank_branch or "").strip() != "")
            or (str(account_number or "").strip() != "")
            or (str(bank_code or "").strip() != "")
        ):
            self._regenerate_ofx_with_overrides(
                analysis_dir,
                closing_balance=closing_balance,
                bank_branch=bank_branch,
                account_number=account_number,
                bank_code=bank_code,
            )
        if file_format == "ofx":
            suffix = "ofx"
        elif file_format == "xlsx":
            suffix = "xlsx"
        else:
            suffix = "csv"
        report_path = analysis_dir / f"converted.{suffix}"
        if not report_path.exists():
            raise AnalysisNotFoundError
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError
        return report_path

    def get_upload_filename(self, analysis_id: str) -> str | None:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
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

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._set_analysis_owner(
            analysis_id=analysis_id,
            metadata_filename="analysis.json",
            identity_type=identity_type,
            identity_id=identity_id,
        )

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        self._assert_analysis_owner(
            analysis_id=analysis_id,
            metadata_filename="analysis.json",
            identity_type=identity_type,
            identity_id=identity_id,
            allow_unowned=allow_unowned,
        )

    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._set_analysis_owner(
            analysis_id=analysis_id,
            metadata_filename="analysis.json",
            identity_type=identity_type,
            identity_id=identity_id,
        )

    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._assert_analysis_owner(
            analysis_id=analysis_id,
            metadata_filename="analysis.json",
            identity_type=identity_type,
            identity_id=identity_id,
            allow_unowned=True,
        )

    def set_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._set_analysis_owner(
            analysis_id=analysis_id,
            metadata_filename="reconcile.json",
            identity_type=identity_type,
            identity_id=identity_id,
        )

    def assert_reconcile_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        self._assert_analysis_owner(
            analysis_id=analysis_id,
            metadata_filename="reconcile.json",
            identity_type=identity_type,
            identity_id=identity_id,
            allow_unowned=allow_unowned,
        )

    def list_convert_history(self, identity_type: str, identity_id: str, limit: int = 20) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        try:
            analysis_dirs = list(self.root_dir.iterdir())
        except OSError:
            analysis_dirs = []

        for analysis_dir in analysis_dirs:
            if not analysis_dir.is_dir():
                continue
            if self._is_expired(analysis_dir):
                try:
                    self._cleanup_analysis(analysis_dir)
                except OSError:
                    pass
                continue

            metadata_path = analysis_dir / "analysis.json"
            if not metadata_path.exists():
                continue

            try:
                content = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            owner_type = str(content.get("owner_identity_type") or "").strip()
            owner_id = str(content.get("owner_identity_id") or "").strip()
            if owner_type != identity_type or owner_id != identity_id:
                continue

            processing_id = str(content.get("analysis_id") or analysis_dir.name).strip()
            created_at = str(content.get("created_at") or content.get("updated_at") or "").strip()
            filename = str(content.get("upload_filename") or "").strip() or f"{processing_id}.pdf"
            model = resolve_conversion_model_label(
                layout_inference_name=str(content.get("layout_inference_name") or "").strip() or None,
                bank_name=str(content.get("bank_name") or "").strip() or None,
            )
            file_type = str(content.get("file_type") or "").strip().lower()
            conversion_type = f"{file_type}-ofx" if file_type else "pdf-ofx"
            status = "Sucesso" if (analysis_dir / "converted.ofx").exists() else "Processando"
            if content.get("financial_document") is not None:
                conversion_type = f"{file_type}-xlsx"
                status = "Sucesso" if (analysis_dir / "converted.xlsx").exists() else "Processando"
                model = {"receivables_report": "Títulos recebidos", "accounts_payable_report": "Contas a pagar",
                         "accounts_receivable_report": "Contas a receber", "investment_statement": "Investimentos CDB",
                         "fiscal_summary": "Resumo fiscal"}.get(str(content.get("document_type")), "Relatório financeiro")

            items.append(
                {
                    "processing_id": processing_id,
                    "created_at": created_at,
                    "filename": filename,
                    "model": model,
                    "conversion_type": conversion_type,
                    "status": status,
                }
            )

        items.sort(key=lambda item: item["created_at"], reverse=True)
        return items[: max(1, min(limit, 100))]

    @locked_analysis
    def get_convert_edit_operation(self, analysis_id: str, operation_id: str) -> dict[str, object] | None:
        content, _ = self._read_analysis_metadata(analysis_id=analysis_id, metadata_filename="analysis.json")
        return self.review_commands.confirm(content, operation_id, analysis_id=analysis_id)

    @staticmethod
    def _write_metadata(path: Path, content: dict) -> None:
        # Readers must never interpret a partially written JSON document as a 404.
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            json.dump(content, target, ensure_ascii=True, indent=2)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @locked_analysis
    def apply_convert_edits(
        self,
        analysis_id: str,
        edits: list[dict[str, object]],
        expected_updated_at: str | None = None,
        opening_balance: float | None = None,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError

        metadata_path = analysis_dir / "analysis.json"
        if not metadata_path.exists():
            raise AnalysisNotFoundError

        try:
            content = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise AnalysisNotFoundError from None

        self._assert_legacy_authority(content)
        result = self.review_commands.apply(
            content, edits, expected_updated_at=expected_updated_at,
            opening_balance=opening_balance, closing_balance=closing_balance,
            bank_branch=bank_branch, account_number=account_number, bank_code=bank_code,
            operation_id=operation_id,
        )
        if result.replayed:
            return self._edit_result(analysis_id, content, operation_id)
        if result.publish_artifacts:
            self._publish_review_artifacts(analysis_dir, content, result)
        self._write_metadata(metadata_path, content)
        return self._edit_result(analysis_id, content, operation_id)

    def _publish_review_artifacts(self, analysis_dir: Path, content: dict, result: ReviewCommandResult) -> None:
        report_rows, preview_rows = result.report_rows, result.preview_rows
        effective_overrides = result.effective_overrides
        opening_balance_value = content["opening_balance"]
        self._write_report_workbook(analysis_dir, content=content, report_rows=report_rows, preview_rows=preview_rows)
        self.export_artifact_service.write_convert_artifacts(
            analysis_dir,
            report_rows=report_rows,
            ofx_account_type=str(content.get("ofx_account_type") or "").strip() or None,
            layout_inference_name=str(content.get("layout_inference_name") or "").strip() or None,
            opening_balance=opening_balance_value,
            closing_balance=effective_overrides.get("closing_balance"),
            balance_bounds_unavailable=bool((content.get("pdf_processing_metrics") or {}).get("balance_bounds_unavailable")),
            bank_branch=effective_overrides.get("bank_branch"),
            account_number=effective_overrides.get("account_number"),
            bank_code=effective_overrides.get("bank_code"),
            allow_layout_bank_fallback=not (content.get("pdf_processing_metrics") or {}).get("statement_issuer_source"),
            **({"financial_document": content["financial_document"]} if content.get("financial_document") is not None else {}),
            **({"statement_accounts": statement_scopes(content.get("document_parts"))}
               if statement_scopes(content.get("document_parts")) else {}),
        )

    def save_reconcile_report(
        self,
        summary: dict[str, int],
        reconciliation_rows: list[dict[str, str | float | None]],
        problems: list[dict[str, str]],
    ) -> tuple[str, str]:
        analysis_id = self._build_analysis_id(prefix="rc")
        analysis_dir = self._resolve_analysis_dir(analysis_id)
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
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError

        suffix = "xlsx" if file_format == "xlsx" else "csv"
        report_path = analysis_dir / f"reconcile_report.{suffix}"
        if not report_path.exists():
            raise AnalysisNotFoundError
        return report_path

    def _regenerate_ofx_with_overrides(
        self,
        analysis_dir: Path,
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> None:
        metadata_path = analysis_dir / "analysis.json"
        if not metadata_path.exists():
            raise AnalysisNotFoundError
        try:
            content = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise AnalysisNotFoundError from None

        result = self.review_commands.prepare_export(
            content, closing_balance=closing_balance, bank_branch=bank_branch,
            account_number=account_number, bank_code=bank_code,
        )
        if result.metadata_changed:
            self._write_metadata(metadata_path, content)
        report_rows, effective_overrides = result.report_rows, result.effective_overrides
        self.export_artifact_service.write_convert_artifacts(
            analysis_dir,
            report_rows=report_rows,
            ofx_account_type=str(content.get("ofx_account_type") or "").strip() or None,
            layout_inference_name=str(content.get("layout_inference_name") or "").strip() or None,
            opening_balance=effective_overrides.get("opening_balance"),
            closing_balance=effective_overrides.get("closing_balance"),
            balance_bounds_unavailable=bool((content.get("pdf_processing_metrics") or {}).get("balance_bounds_unavailable")),
            bank_branch=effective_overrides.get("bank_branch"),
            account_number=effective_overrides.get("account_number"),
            bank_code=effective_overrides.get("bank_code"),
            allow_layout_bank_fallback=not (content.get("pdf_processing_metrics") or {}).get("statement_issuer_source"),
            **({"financial_document": content["financial_document"]} if content.get("financial_document") is not None else {}),
            **({"statement_accounts": statement_scopes(content.get("document_parts"))}
               if statement_scopes(content.get("document_parts")) else {}),
        )

    def _write_report_workbook(
        self,
        analysis_dir: Path,
        content: dict[str, object],
        report_rows: list[TransactionRow],
        preview_rows: list[TransactionRow],
    ) -> None:
        snapshot = self._build_analysis_snapshot_from_content(
            content,
            preview_rows=preview_rows,
            report_rows=report_rows,
        )
        self.export_artifact_service.write_analysis_report_workbook(
            analysis_dir / "report.xlsx",
            report_rows=report_rows,
            snapshot=snapshot,
        )

    def _set_analysis_owner(
        self,
        *,
        analysis_id: str,
        metadata_filename: str,
        identity_type: str,
        identity_id: str,
    ) -> None:
        content, metadata_path = self._read_analysis_metadata(
            analysis_id=analysis_id,
            metadata_filename=metadata_filename,
        )

        owner_type = str(content.get("owner_identity_type") or "").strip()
        owner_id = str(content.get("owner_identity_id") or "").strip()
        if owner_type and owner_id:
            if owner_type != identity_type or owner_id != identity_id:
                raise AnalysisAccessDeniedError
            return

        content["owner_identity_type"] = identity_type
        content["owner_identity_id"] = identity_id
        self._write_metadata(metadata_path, content)

    def _assert_analysis_owner(
        self,
        *,
        analysis_id: str,
        metadata_filename: str,
        identity_type: str,
        identity_id: str,
        allow_unowned: bool,
    ) -> None:
        content, _metadata_path = self._read_analysis_metadata(
            analysis_id=analysis_id,
            metadata_filename=metadata_filename,
        )

        owner_type = str(content.get("owner_identity_type") or "").strip()
        owner_id = str(content.get("owner_identity_id") or "").strip()
        if not owner_type and not owner_id:
            if allow_unowned:
                return
            raise AnalysisAccessDeniedError
        if owner_type != identity_type or owner_id != identity_id:
            raise AnalysisAccessDeniedError

    def _read_analysis_metadata(self, *, analysis_id: str, metadata_filename: str) -> tuple[dict[str, object], Path]:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        if self._is_expired(analysis_dir):
            self._cleanup_analysis(analysis_dir)
            raise AnalysisNotFoundError

        metadata_path = analysis_dir / metadata_filename
        if not metadata_path.exists():
            raise AnalysisNotFoundError
        try:
            content = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise AnalysisNotFoundError from None
        if metadata_filename == "analysis.json":
            self._assert_legacy_authority(content)
        return content, metadata_path

    @staticmethod
    def _assert_legacy_authority(content: dict) -> None:
        if content.get("authority") in {"postgres_v1", "canonical_pending"}:
            raise AnalysisEditConflictError

    @locked_analysis
    def fence_canonical_analysis(self, analysis_id: str, *, completion: dict | None = None) -> dict:
        """Fence a new, not-yet-exposed PRO document before SQL adoption.

        A retry can recover the extraction from the pending marker. Old binaries
        must be drained before rollout; their code cannot honor this barrier.
        """
        path = self._resolve_analysis_dir(analysis_id) / "analysis.json"
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(content["expires_at"])
        except (OSError, KeyError, ValueError):
            raise AnalysisNotFoundError from None
        if expires_at.tzinfo is None or self.now_provider() >= expires_at:
            raise AnalysisNotFoundError
        if content.get("authority") not in {None, "canonical_pending"}:
            raise AnalysisEditConflictError
        if completion is not None:
            previous = content.get("canonical_completion")
            if previous is not None and previous != completion:
                raise AnalysisEditConflictError
            content["canonical_completion"] = completion
        content["authority"] = "canonical_pending"
        self._write_metadata(path, content)
        return {key: value for key, value in content.items() if key not in {"authority", "canonical_completion"}}

    @locked_analysis
    def get_canonical_completion(self, analysis_id: str) -> dict | None:
        """Internal retry checkpoint, immutable and bounded by the extraction TTL."""
        path = self._resolve_analysis_dir(analysis_id) / "analysis.json"
        if not path.exists():
            return None
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
            if content.get("authority") not in {"canonical_pending", "postgres_v1"}:
                return None
            expires_at = datetime.fromisoformat(content["expires_at"])
        except (OSError, KeyError, ValueError):
            raise AnalysisNotFoundError from None
        if expires_at.tzinfo is None or self.now_provider() >= expires_at:
            raise AnalysisNotFoundError
        completion = content.get("canonical_completion")
        if not isinstance(completion, dict):
            raise AnalysisEditConflictError
        return completion

    @locked_analysis
    def delete_canonical_analysis(self, analysis_id: str) -> None:
        """Internal retained-data cleanup; caller supplies a SQL expiration/tombstone."""
        directory = self._resolve_analysis_dir(analysis_id)
        metadata = directory / "analysis.json"
        if metadata.exists():
            content = json.loads(metadata.read_text(encoding="utf-8"))
            if content.get("authority") not in {"postgres_v1", "canonical_pending"}:
                raise ValueError("Cannot purge a legacy analysis through canonical cleanup")
        self._cleanup_analysis(directory)

    def _resolve_analysis_dir(self, analysis_id: str) -> Path:
        clean_analysis_id = str(analysis_id or "").strip()
        if not _SAFE_ANALYSIS_ID_RE.fullmatch(clean_analysis_id):
            raise AnalysisNotFoundError
        return self.root_dir / clean_analysis_id

    def _is_expired(self, analysis_dir: Path) -> bool:
        metadata_path = analysis_dir / "analysis.json"
        try:
            content = json.loads(metadata_path.read_text(encoding="utf-8"))
            expires_at_raw = content.get("expires_at")
            if not isinstance(expires_at_raw, str):
                return False
            expires_at = datetime.fromisoformat(expires_at_raw)
            expired = self.now_provider() > expires_at
            if not expired:
                self._assert_legacy_authority(content)
            return expired
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

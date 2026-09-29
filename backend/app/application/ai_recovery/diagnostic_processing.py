from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Protocol

from app.application.ai_recovery.comparator import (
    ComparisonLedger,
    ComparisonTransaction,
    compare_recovery_ledgers,
)
from app.application.ai_recovery.models import TransactionDirection
from app.application.ai_recovery.schemas import AIRecoveryRequestManifest, NovaStatementV2

logger = logging.getLogger(__name__)


class AIRecoveryObjectReader(Protocol):
    def get_bytes(self, *, bucket: str, key: str) -> bytes: ...


class NovaDiagnosticAnalyzer(Protocol):
    def analyze(self, **kwargs): ...


class AIRecoveryFixtureBuilder(Protocol):
    def build(self, **kwargs): ...


class AIRecoveryResultStore(Protocol):
    def store_restricted(self, *, manifest: AIRecoveryRequestManifest, diagnostic: dict[str, object]) -> None: ...

    def store_fixture(self, *, manifest: AIRecoveryRequestManifest, fixture) -> None: ...


@dataclass(frozen=True, slots=True)
class AIRecoveryProcessingOutcome:
    status: str
    idempotency_key: str
    fixture_status: str


class AIRecoveryDiagnosticProcessor:
    def __init__(
        self,
        *,
        object_reader: AIRecoveryObjectReader,
        analyzer: NovaDiagnosticAnalyzer,
        result_store: AIRecoveryResultStore,
        fixture_builder: AIRecoveryFixtureBuilder,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.object_reader = object_reader
        self.analyzer = analyzer
        self.result_store = result_store
        self.fixture_builder = fixture_builder
        self.clock = clock or (lambda: datetime.now(UTC))

    def process(
        self,
        *,
        bucket: str,
        ready_key: str,
        expected_idempotency_key: str,
    ) -> AIRecoveryProcessingOutcome:
        manifest = AIRecoveryRequestManifest.model_validate_json(
            self.object_reader.get_bytes(bucket=bucket, key=ready_key)
        )
        self._validate_manifest(
            manifest=manifest,
            bucket=bucket,
            ready_key=ready_key,
            expected_idempotency_key=expected_idempotency_key,
        )
        already_stored = getattr(self.result_store, "restricted_exists", None)
        if callable(already_stored) and already_stored(manifest=manifest):
            return AIRecoveryProcessingOutcome("already_completed", manifest.idempotency_key, "unchanged")
        original_pdf = self.object_reader.get_bytes(bucket=bucket, key=manifest.document.key)
        if len(original_pdf) != manifest.document.size_bytes:
            raise ValueError("AI recovery PDF size does not match manifest.")
        if hashlib.sha256(original_pdf).hexdigest() != manifest.document.sha256:
            raise ValueError("AI recovery PDF digest does not match manifest.")
        deterministic_bytes = self.object_reader.get_bytes(
            bucket=bucket, key=manifest.deterministic_artifact.key
        )
        _validate_optional_digest(
            deterministic_bytes,
            manifest.deterministic_artifact.sha256,
            name="deterministic artifact",
        )
        deterministic = _load_json_object(
            deterministic_bytes,
            name="deterministic artifact",
        )
        source_evidence_bytes = self.object_reader.get_bytes(
            bucket=bucket,
            key=manifest.deterministic_artifact.source_evidence_key,
        )
        _validate_optional_digest(
            source_evidence_bytes,
            manifest.deterministic_artifact.source_evidence_sha256,
            name="source evidence",
        )
        source_evidence = _load_json_object(
            source_evidence_bytes,
            name="source evidence",
        )
        result = self.analyzer.analyze(
            filename="original.pdf",
            raw_bytes=original_pdf,
            page_count=manifest.document.page_count,
            deterministic_artifact=deterministic,
        )
        comparison = compare_recovery_ledgers(
            deterministic=_deterministic_ledger(deterministic),
            nova=_nova_ledger(result.diagnostic.statement),
        )
        diagnostic = {
            "schema_version": "ai_recovery_diagnostic_v1",
            "classification": "restricted",
            "idempotency_key": manifest.idempotency_key,
            "analysis_id": manifest.analysis_id,
            "model_id": result.model_id,
            "prompt_version": result.prompt_version,
            "provider_request_id": result.provider_request_id,
            "latency_ms": result.latency_ms,
            "usage": (
                {
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                }
                if result.usage is not None
                else None
            ),
            "nova": result.diagnostic.model_dump(mode="json"),
            "comparison": _comparison_payload(comparison),
            "created_at": self.clock().isoformat(),
        }
        self.result_store.store_restricted(manifest=manifest, diagnostic=diagnostic)

        fixture_status = "stored"
        try:
            fixture = self.fixture_builder.build(
                original_pdf=original_pdf,
                manifest=manifest,
                deterministic_artifact=deterministic,
                source_evidence=source_evidence,
                diagnostic=result.diagnostic,
                comparison=comparison,
            )
            self.result_store.store_fixture(manifest=manifest, fixture=fixture)
        except Exception as exc:
            fixture_status = "withheld_privacy_or_generation_failure"
            logger.warning(
                "ai_recovery_fixture_withheld idempotency_key=%s error_type=%s",
                manifest.idempotency_key,
                type(exc).__name__,
            )
        return AIRecoveryProcessingOutcome("completed", manifest.idempotency_key, fixture_status)

    def _validate_manifest(
        self,
        *,
        manifest: AIRecoveryRequestManifest,
        bucket: str,
        ready_key: str,
        expected_idempotency_key: str,
    ) -> None:
        if manifest.idempotency_key != expected_idempotency_key:
            raise ValueError("AI recovery queue idempotency key does not match manifest.")
        if manifest.document.bucket != bucket:
            raise ValueError("AI recovery queue bucket does not match manifest.")
        expected_base = manifest.document.key.removesuffix("/input.pdf")
        expected_keys = {
            f"{expected_base}/ready.json",
            f"{expected_base}/deterministic.json",
            f"{expected_base}/source-evidence.json",
        }
        referenced_keys = {
            ready_key,
            manifest.deterministic_artifact.key,
            manifest.deterministic_artifact.source_evidence_key,
        }
        if referenced_keys != expected_keys:
            raise ValueError("AI recovery request object keys do not share the immutable request prefix.")
        if manifest.expires_at <= self.clock():
            raise ValueError("AI recovery request has expired.")
        if manifest.ai.output_schema_version != "nova_diagnostic_v1":
            raise ValueError("AI recovery request does not use the diagnostic output contract.")
        if (
            manifest.deterministic_artifact.sha256 is None
            or manifest.deterministic_artifact.source_evidence_sha256 is None
        ):
            raise ValueError("AI recovery diagnostic request requires artifact digests.")


def _load_json_object(raw: bytes, *, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError(f"AI recovery {name} is not valid JSON.") from None
    if not isinstance(value, dict):
        raise ValueError(f"AI recovery {name} must be a JSON object.")
    return value


def _validate_optional_digest(raw: bytes, expected: str | None, *, name: str) -> None:
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"AI recovery {name} digest does not match manifest.")


def _deterministic_ledger(payload: Mapping[str, object]) -> ComparisonLedger:
    rows = payload.get("transactions")
    transactions: list[ComparisonTransaction] = []
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        signed_amount = _decimal(row.get("amount"))
        issues = row.get("source_issues")
        evidence_ids = tuple(
            f"p{issue.get('source_page')}:l{issue.get('source_line')}"
            for issue in issues if isinstance(issue, dict) and issue.get("source_page") and issue.get("source_line")
        ) if isinstance(issues, list) else ()
        transactions.append(
            ComparisonTransaction(
                date=_date(row.get("date")),
                description_lines=(str(row.get("description") or "[sem descricao]"),),
                amount=abs(signed_amount),
                direction=(TransactionDirection.CREDIT if signed_amount >= 0 else TransactionDirection.DEBIT),
                running_balance=_optional_decimal(row.get("running_balance")),
                page=_first_issue_page(issues),
                visual_order=index + 1,
                evidence_ids=evidence_ids,
            )
        )
    return ComparisonLedger(
        transactions=tuple(transactions),
        opening_balance=_optional_decimal(payload.get("opening_balance")),
        closing_balance=_optional_decimal(payload.get("closing_balance")),
    )


def _nova_ledger(statement: NovaStatementV2) -> ComparisonLedger:
    transactions = tuple(
        ComparisonTransaction(
            date=_date(row.date_text),
            description_lines=tuple(row.description_lines),
            amount=abs(_decimal(row.amount_text)),
            direction=TransactionDirection(row.direction.value),
            running_balance=_optional_decimal(row.running_balance_text),
            page=row.page,
            visual_order=row.visual_order,
            evidence_ids=(f"p{row.page}:l{row.visual_line_start}",),
        )
        for row in statement.transactions
    )
    return ComparisonLedger(
        transactions=transactions,
        opening_balance=_balance(statement.opening_balance),
        closing_balance=_balance(statement.closing_balance),
    )


def _balance(value) -> Decimal | None:
    if value is None:
        return None
    amount = abs(_decimal(value.amount_text))
    return -amount if value.direction.value == "debit" else amount


def _comparison_payload(comparison) -> dict[str, object]:
    return {
        "comparator_version": comparison.comparator_version,
        "exact": comparison.exact,
        "financially_equivalent": comparison.financially_equivalent,
        "evidence_coverage": str(comparison.evidence_coverage),
        "deterministic_unmatched": list(comparison.deterministic_unmatched),
        "nova_unmatched": list(comparison.nova_unmatched),
        "diagnoses": [
            {
                "code": item.code.value,
                "deterministic_indexes": list(item.deterministic_indexes),
                "nova_indexes": list(item.nova_indexes),
                "evidence_ids": list(item.evidence_ids),
            }
            for item in comparison.diagnoses
        ],
    }


def _decimal(value: object) -> Decimal:
    raw = str(value or "0").strip().replace("R$", "").replace(" ", "")
    negative = raw.endswith("D") or raw.startswith("-")
    raw = raw.rstrip("CDcd").lstrip("+-")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        parsed = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("Invalid monetary text in AI recovery artifact.") from None
    return -parsed if negative else parsed


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None or value == "" else _decimal(value)


def _date(value: object) -> date | None:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _first_issue_page(issues: object) -> int | None:
    if not isinstance(issues, list):
        return None
    for issue in issues:
        if isinstance(issue, dict) and issue.get("source_page") is not None:
            return max(1, int(issue["source_page"]))
    return None

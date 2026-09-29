from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader

from app.application.ai_recovery.schemas import AIRecoveryRequestManifest, NovaDiagnosticV1
from app.application.conversion.canonical_layout_capture import CanonicalLayoutGenerator
from app.application.conversion.uploaded_document import UploadedDocument
from app.application.conversion.v3.layout import sanitize_semantic_line
from app.application.conversion.v3.privacy import validate_v3_output
from app.application.document_extraction_models import ExtractedLine

_SAFE_FINANCIAL_WORDS = frozenset(
    {
        "A",
        "AGENCIA",
        "ANTERIOR",
        "APLICACAO",
        "ATUAL",
        "BANCO",
        "BRADESCO",
        "BRASIL",
        "CAIXA",
        "CLIENTE",
        "CNPJ",
        "COMPRA",
        "CONTA",
        "CORRENTE",
        "CPF",
        "CRED",
        "CREDITO",
        "DA",
        "DATA",
        "DE",
        "DEB",
        "DEBITO",
        "DEPOSITO",
        "DESCRICAO",
        "DOC",
        "DOCUMENTO",
        "DO",
        "EFETIVA",
        "EM",
        "ENVIADO",
        "ENTRADA",
        "ESTORNO",
        "EXTRATO",
        "EMAIL",
        "ENDERECO",
        "FINAL",
        "HISTORICO",
        "IDENTIFICADOR",
        "INICIAL",
        "INTER",
        "ITAU",
        "LANCAMENTO",
        "LANCAMENTOS",
        "MOVIMENTACAO",
        "NUBANK",
        "PAGAMENTO",
        "PAGINA",
        "PARTE",
        "PERIODO",
        "PIX",
        "R",
        "RECEBIDO",
        "RECEBIMENTO",
        "RESGATE",
        "SAIDA",
        "SALDO",
        "SANTANDER",
        "SAQUE",
        "TARIFA",
        "TED",
        "TELEFONE",
        "TIPO",
        "TITULAR",
        "TOTAL",
        "TRANSACAO",
        "TRANSACOES",
        "TRANSFERENCIA",
        "VALOR",
    }
)
_WORD_PATTERN = re.compile(r"[A-Z]+")


@dataclass(frozen=True, slots=True)
class PrivacySafeFixture:
    pdf_bytes: bytes
    manifest: dict[str, object]
    expected: dict[str, object]
    readme: str


class CanonicalV3PrivacyFixtureBuilder:
    """Build a shareable reproduction only after the restricted diagnosis completes."""

    def __init__(self, *, max_pages: int = 15) -> None:
        self.generator = CanonicalLayoutGenerator(schema_version="3", max_pages=max_pages)

    def build(
        self,
        *,
        original_pdf: bytes,
        manifest: AIRecoveryRequestManifest,
        deterministic_artifact: dict[str, object],
        source_evidence: dict[str, object],
        diagnostic: NovaDiagnosticV1,
        comparison,
    ) -> PrivacySafeFixture:
        pages = source_evidence.get("pages")
        page_records = pages if isinstance(pages, list) else []
        source_page_texts = tuple(
            str(page.get("text") or "") for page in page_records if isinstance(page, dict)
        )
        if not source_page_texts or not any(text.strip() for text in source_page_texts):
            raise ValueError("AI recovery fixture requires extracted page text for privacy preprocessing.")
        if len(source_page_texts) != manifest.document.page_count:
            raise ValueError("AI recovery fixture source evidence does not cover every PDF page.")
        page_texts = tuple(
            "\n".join(_strict_sanitize_line(line) for line in text.splitlines())
            for text in source_page_texts
        )
        layout_lines = _layout_lines(page_records)
        transactions = deterministic_artifact.get("transactions")
        transaction_rows = transactions if isinstance(transactions, list) else []
        selected_parser = str(deterministic_artifact.get("selected_parser") or "unknown")
        artifact = self.generator.generate(
            document=UploadedDocument(filename="original.pdf", file_type="pdf", raw_bytes=original_pdf),
            status="Sucesso",
            conversion_type="pdf-ofx",
            transactions_count=len(transaction_rows),
            layout_name=manifest.deterministic_artifact.layout_profile,
            layout_confidence=manifest.deterministic_artifact.layout_confidence,
            selected_parser=selected_parser,
            warning_count=len(manifest.deterministic_artifact.issue_codes),
            balance_failed=int("balance_consistency_failed" in manifest.deterministic_artifact.issue_codes),
            page_texts=page_texts,
            # Force the generator to use privacy-preprocessed evidence instead of native source text.
            page_text_source="ocr",
            source_layout_lines=layout_lines,
        )
        if not artifact.privacy_validated:
            raise ValueError("AI recovery fixture did not pass canonical privacy validation.")

        expected_transactions = []
        source_descriptions: list[str] = []
        for row in diagnostic.statement.transactions:
            source_description = " ".join(row.description_lines)
            source_descriptions.append(source_description)
            expected_transactions.append(
                {
                    "visual_order": row.visual_order,
                    "page": row.page,
                    "date_text": row.date_text,
                    "description": _strict_sanitize_line(source_description),
                    "amount_text": row.amount_text,
                    "direction": row.direction.value,
                    "running_balance_text": row.running_balance_text,
                }
            )
        expected = {
            "schema_version": "ai_recovery_fixture_expected_v1",
            "layout_profile": manifest.deterministic_artifact.layout_profile,
            "issue_codes": list(manifest.deterministic_artifact.issue_codes),
            "nova_conclusion": diagnostic.conclusion,
            "nova_finding_codes": [finding.code for finding in diagnostic.findings],
            "comparator_diagnosis_codes": [item.code.value for item in comparison.diagnoses],
            "transactions": expected_transactions,
        }
        visible_fixture_text = "\n".join(
            page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages
        )
        fixture_text = visible_fixture_text + str(expected) + str(artifact.manifest)
        validate_v3_output(source_text="", output_text=fixture_text)
        _validate_strict_source_privacy(
            source_text="\n".join(source_page_texts + tuple(source_descriptions)),
            output_text=fixture_text,
        )
        fixture_manifest = {
            **artifact.manifest,
            "privacy_validated": True,
            "source_classification": "restricted_original_not_included",
            "diagnosis_idempotency_key": manifest.idempotency_key,
        }
        readme = (
            "# Reprodução anonimizada de warning de transação\n\n"
            "Este pacote foi criado após análise restrita do documento original. "
            "O PDF original e a resposta bruta do modelo não fazem parte da fixture.\n\n"
            "Use `input.pdf` para reproduzir o comportamento do parser e `expected.json` "
            "para conferir as transações e os códigos de diagnóstico esperados.\n"
        )
        return PrivacySafeFixture(artifact.pdf_bytes, fixture_manifest, expected, readme)


def _layout_lines(page_records: list[object]) -> tuple[tuple[ExtractedLine, ...], ...]:
    pages: list[tuple[ExtractedLine, ...]] = []
    for page_index, page in enumerate(page_records):
        if not isinstance(page, dict):
            pages.append(())
            continue
        raw_lines = page.get("lines")
        lines: list[ExtractedLine] = []
        for fallback_index, item in enumerate(raw_lines if isinstance(raw_lines, list) else []):
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox")
            lines.append(
                ExtractedLine(
                    id=str(item.get("id") or f"p{page_index + 1}-l{fallback_index + 1}"),
                    page_number=page_index + 1,
                    line_index=max(1, int(item.get("line_index") or fallback_index + 1)),
                    text=_strict_sanitize_line(str(item.get("text") or "")),
                    bbox=bbox if isinstance(bbox, dict) else None,
                )
            )
        if not lines:
            for line_index, text in enumerate(str(page.get("text") or "").splitlines(), start=1):
                lines.append(
                    ExtractedLine(
                        id=f"p{page_index + 1}-l{line_index}",
                        page_number=page_index + 1,
                        line_index=line_index,
                        text=_strict_sanitize_line(text),
                    )
                )
        pages.append(tuple(lines))
    return tuple(pages)


def _strict_sanitize_line(value: str) -> str:
    sanitized = sanitize_semantic_line(value, in_transaction_table=True)
    normalized = _ascii_upper(sanitized)

    def replace_word(match: re.Match[str]) -> str:
        word = match.group(0)
        return word if word in _SAFE_FINANCIAL_WORDS or len(word) <= 1 else "[PARTE]"

    masked = _WORD_PATTERN.sub(replace_word, normalized)
    masked = re.sub(r"(?:\[PARTE\]\s*){2,}", "[PARTE] ", masked)
    return " ".join(masked.split())


def _ascii_upper(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").replace("\u00a0", " "))
    return "".join(character for character in decomposed if not unicodedata.combining(character)).upper()


def _validate_strict_source_privacy(*, source_text: str, output_text: str) -> None:
    normalized_output = _ascii_upper(output_text)
    private_words = {
        word
        for word in _WORD_PATTERN.findall(_ascii_upper(source_text))
        if len(word) >= 3 and word not in _SAFE_FINANCIAL_WORDS
    }
    if any(re.search(rf"\b{re.escape(word)}\b", normalized_output) for word in private_words):
        raise ValueError("ai_recovery_fixture_source_identity_leak_detected")

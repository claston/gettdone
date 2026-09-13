from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Protocol
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.conversion.uploaded_document import UploadedDocument
from app.application.conversion_quality import ConversionQualityAssessment, assess_conversion_quality

logger = logging.getLogger(__name__)

CANONICAL_LAYOUT_SCHEMA_VERSION = "1"
CANONICAL_PRIVACY_VALIDATION_VERSION = "1"
_SAFE_LABELS = frozenset(
    {
        "A",
        "AGENCIA",
        "ANTERIOR",
        "ATUAL",
        "BANCO",
        "BRADESCO",
        "BRL",
        "CAIXA",
        "CLIENTE",
        "CNPJ",
        "CONTA",
        "CORRENTE",
        "CPF",
        "CREDITO",
        "DA",
        "DATA",
        "DE",
        "DEBITO",
        "DESCRICAO",
        "DO",
        "DOCUMENTO",
        "E",
        "EM",
        "ENTRADA",
        "ESTORNO",
        "EXTRATO",
        "FINAL",
        "HISTORICO",
        "INICIAL",
        "INTER",
        "ITAU",
        "LANCAMENTO",
        "LANCAMENTOS",
        "MOVIMENTACAO",
        "NA",
        "NO",
        "NUBANK",
        "PAGAMENTO",
        "PAGBANK",
        "PAGINA",
        "PARA",
        "PERIODO",
        "PIX",
        "REAL",
        "REAIS",
        "RECEBIMENTO",
        "R",
        "SAIDA",
        "SALDO",
        "SANTANDER",
        "SICOOB",
        "SICREDI",
        "TARIFA",
        "TED",
        "TOTAL",
        "TRANSACAO",
        "TRANSACOES",
        "TRANSFERENCIA",
        "VALOR",
    }
)
_CAPTURE_ID_PATTERN = re.compile(r"^cap_[a-f0-9]{24}$")
_DATE_DMY_PATTERN = re.compile(r"^\d{1,2}([/-])\d{1,2}\1\d{2,4}$")
_DATE_YMD_PATTERN = re.compile(r"^\d{4}([-/.])\d{1,2}\1\d{1,2}$")
_TOKEN_PATTERN = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class CanonicalLayoutArtifact:
    capture_id: str
    pdf_bytes: bytes
    manifest: dict[str, object]
    privacy_validated: bool


@dataclass(frozen=True, slots=True)
class CanonicalLayoutCaptureResult:
    status: str
    reason: str | None = None


class CanonicalLayoutStore(Protocol):
    def store(self, artifact: CanonicalLayoutArtifact) -> None: ...


class CanonicalLayoutUnsupportedError(ValueError):
    pass


class CanonicalLayoutPrivacyError(ValueError):
    pass


class CanonicalLayoutGenerator:
    def __init__(
        self,
        *,
        max_pages: int = 20,
        max_extracted_chars: int = 250_000,
        capture_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.max_pages = max(1, int(max_pages))
        self.max_extracted_chars = max(1, int(max_extracted_chars))
        self.capture_id_provider = capture_id_provider or (lambda: f"cap_{uuid4().hex[:24]}")

    def generate(
        self,
        *,
        document: UploadedDocument,
        status: str,
        conversion_type: str,
        transactions_count: int | None,
        layout_name: str | None,
        layout_confidence: float | None,
        selected_parser: str | None,
        warning_count: int,
        balance_failed: int,
    ) -> CanonicalLayoutArtifact:
        if document.file_type != "pdf":
            raise CanonicalLayoutUnsupportedError("non_pdf")
        assessment = assess_conversion_quality(
            status=status,
            conversion_type=conversion_type,
            transactions_count=transactions_count,
            layout_name=layout_name,
            layout_confidence=layout_confidence,
            selected_parser=selected_parser,
            warning_count=warning_count,
            balance_failed=balance_failed,
        )
        pages = self._read_pages(document.raw_bytes)
        source_text = "\n".join(page["source_text"] for page in pages)
        if len(source_text) > self.max_extracted_chars:
            raise CanonicalLayoutUnsupportedError("native_text_too_large")

        manifest_pages: list[dict[str, object]] = []
        render_pages: list[dict[str, object]] = []
        number_index = 0
        for page in pages:
            source_lines = str(page["source_text"]).splitlines()
            elements: list[dict[str, object]] = []
            max_columns = max((len(line) for line in source_lines), default=1)
            line_count = max(1, len(source_lines))
            for line_index, line in enumerate(source_lines):
                for match in _TOKEN_PATTERN.finditer(line):
                    safe_text, role, number_index = _sanitize_token(match.group(0), number_index=number_index)
                    elements.append(
                        {
                            "line": line_index,
                            "column": match.start(),
                            "x": round(match.start() / max(1, max_columns), 6),
                            "y": round(line_index / line_count, 6),
                            "role": role,
                            "text": safe_text,
                        }
                    )
            manifest_pages.append(
                {
                    "width": round(float(page["width"]), 3),
                    "height": round(float(page["height"]), 3),
                    "line_count": line_count,
                    "max_columns": max_columns,
                    "elements": elements,
                }
            )
            render_pages.append({**page, "line_count": line_count, "max_columns": max_columns, "elements": elements})

        capture_id = self.capture_id_provider()
        if _CAPTURE_ID_PATTERN.fullmatch(capture_id) is None:
            raise ValueError("Invalid canonical capture id.")
        manifest: dict[str, object] = {
            "schema_version": CANONICAL_LAYOUT_SCHEMA_VERSION,
            "privacy_validation_version": CANONICAL_PRIVACY_VALIDATION_VERSION,
            "quality": _quality_manifest(assessment, layout_name=layout_name, selected_parser=selected_parser),
            "pages": manifest_pages,
        }
        pdf_bytes = _render_pdf(render_pages)
        _validate_privacy(source_text=source_text, pdf_bytes=pdf_bytes, manifest=manifest)
        return CanonicalLayoutArtifact(
            capture_id=capture_id,
            pdf_bytes=pdf_bytes,
            manifest=manifest,
            privacy_validated=True,
        )

    def _read_pages(self, raw_bytes: bytes) -> list[dict[str, object]]:
        try:
            reader = PdfReader(BytesIO(raw_bytes))
            if reader.is_encrypted:
                raise CanonicalLayoutUnsupportedError("encrypted_pdf")
            if len(reader.pages) > self.max_pages:
                raise CanonicalLayoutUnsupportedError("page_limit_exceeded")
            pages: list[dict[str, object]] = []
            has_native_text = False
            for page in reader.pages:
                if "/Contents" not in page:
                    text = ""
                else:
                    text = page.extract_text(extraction_mode="layout") or ""
                has_native_text = has_native_text or bool(text.strip())
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                if not (72 <= width <= 2_000 and 72 <= height <= 2_000):
                    raise CanonicalLayoutUnsupportedError("unsupported_page_dimensions")
                pages.append({"source_text": text, "width": width, "height": height})
        except CanonicalLayoutUnsupportedError:
            raise
        except Exception as exc:
            raise CanonicalLayoutUnsupportedError("invalid_pdf") from exc
        if not pages or not has_native_text:
            raise CanonicalLayoutUnsupportedError("native_text_unavailable")
        return pages


class CanonicalLayoutCaptureService:
    def __init__(
        self,
        *,
        enabled: bool,
        generator: CanonicalLayoutGenerator | None = None,
        store: CanonicalLayoutStore | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.generator = generator
        self.store = store
        if self.enabled and (self.generator is None or self.store is None):
            raise ValueError("Enabled canonical layout capture requires a generator and store.")

    def capture(self, *, document: UploadedDocument, **quality_values: object) -> CanonicalLayoutCaptureResult:
        if not self.enabled:
            return CanonicalLayoutCaptureResult("disabled")
        if document.file_type != "pdf":
            return CanonicalLayoutCaptureResult("not_eligible")
        assessment = assess_conversion_quality(**quality_values)
        if assessment.status in {"clean", "processing"}:
            return CanonicalLayoutCaptureResult("not_eligible")
        assert self.generator is not None
        assert self.store is not None
        try:
            artifact = self.generator.generate(document=document, **quality_values)
        except CanonicalLayoutUnsupportedError as exc:
            return CanonicalLayoutCaptureResult("skipped_unsupported", str(exc))
        except CanonicalLayoutPrivacyError as exc:
            return CanonicalLayoutCaptureResult("skipped_privacy", str(exc))
        except Exception as exc:  # fail closed without exposing source/error text
            logger.warning("canonical_layout_generation_failed error_type=%s", exc.__class__.__name__)
            return CanonicalLayoutCaptureResult("generation_failed", exc.__class__.__name__)
        try:
            self.store.store(artifact)
        except Exception as exc:  # capture is best effort and must never fail a conversion
            logger.warning("canonical_layout_upload_failed error_type=%s", exc.__class__.__name__)
            return CanonicalLayoutCaptureResult("upload_failed", exc.__class__.__name__)
        return CanonicalLayoutCaptureResult("stored")


def _sanitize_token(raw_token: str, *, number_index: int) -> tuple[str, str, int]:
    normalized = _ascii_upper(raw_token)
    core = "".join(character for character in normalized if character.isalpha())
    if not any(character.isdigit() for character in normalized) and core in _SAFE_LABELS:
        return core, "label", number_index
    if _DATE_DMY_PATTERN.fullmatch(normalized):
        separator = "/" if "/" in normalized else "-"
        replacement = f"03{separator}01{separator}2001"
        if replacement == normalized:
            replacement = f"04{separator}02{separator}2002"
        return replacement, "date", number_index + 1
    if _DATE_YMD_PATTERN.fullmatch(normalized):
        separator = next(character for character in normalized if character in "-/. ".strip())
        replacement = f"2001{separator}01{separator}03"
        if replacement == normalized:
            replacement = f"2002{separator}02{separator}04"
        return replacement, "date", number_index + 1
    if any(character.isdigit() for character in normalized):
        return _replace_digits(normalized, offset=number_index), "number", number_index + 1
    return "DADO", "text", number_index


def _replace_digits(value: str, *, offset: int) -> str:
    result: list[str] = []
    digit_index = 0
    for character in value:
        if character.isdigit():
            shift = 1 + ((offset + digit_index) % 9)
            result.append(str((int(character) + shift) % 10))
            digit_index += 1
        elif character.isalpha():
            result.append("X")
        elif character in ".,/:()+-$%":
            result.append(character)
    return "".join(result) or "0"


def _ascii_upper(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in decomposed if not unicodedata.combining(character)).upper()


def _quality_manifest(
    assessment: ConversionQualityAssessment,
    *,
    layout_name: str | None,
    selected_parser: str | None,
) -> dict[str, object]:
    safe_layout = _safe_code(layout_name) or "unknown"
    safe_parser = _safe_code(selected_parser) or "unknown"
    return {
        "status": assessment.status,
        "score": round(assessment.score, 4) if assessment.score is not None else None,
        "layout_name": safe_layout,
        "selected_parser": safe_parser,
        "reason_codes": list(assessment.reason_codes),
        "rule_version": assessment.rule_version,
    }


def _safe_code(value: object) -> str | None:
    normalized = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", normalized) is None:
        return None
    return normalized


def _render_pdf(pages: list[dict[str, object]]) -> bytes:
    writer = PdfWriter()
    writer.add_metadata(
        {
            "/Title": "Synthetic canonical bank statement",
            "/Producer": "OFX Simples canonical layout generator",
        }
    )
    for source_page in pages:
        width = float(source_page["width"])
        height = float(source_page["height"])
        page = writer.add_blank_page(width=width, height=height)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Courier"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        line_count = max(1, int(source_page["line_count"]))
        max_columns = max(1, int(source_page["max_columns"]))
        horizontal_margin = 24.0
        vertical_margin = 28.0
        column_width = max(2.5, (width - (2 * horizontal_margin)) / max_columns)
        font_size = min(10.0, max(5.0, column_width / 0.6))
        line_height = min(font_size * 1.5, max(6.0, (height - (2 * vertical_margin)) / line_count))
        commands: list[str] = []
        for element in source_page["elements"]:
            x = horizontal_margin + (int(element["column"]) * column_width)
            y = height - vertical_margin - (int(element["line"]) * line_height)
            safe_text = _escape_pdf_text(str(element["text"]))
            commands.extend(
                [
                    "BT",
                    f"/F1 {font_size:.3f} Tf",
                    f"1 0 0 1 {x:.3f} {y:.3f} Tm",
                    f"({safe_text}) Tj",
                    "ET",
                ]
            )
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _validate_privacy(*, source_text: str, pdf_bytes: bytes, manifest: dict[str, object]) -> None:
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        output_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise CanonicalLayoutPrivacyError("canonical_pdf_unreadable") from exc
    serialized_output = _ascii_upper(f"{output_text}\n{manifest}")
    output_digit_tokens = {
        "".join(character for character in token if character.isdigit())
        for token in _TOKEN_PATTERN.findall(_ascii_upper(output_text))
    }
    for raw_token in _TOKEN_PATTERN.findall(_ascii_upper(source_text)):
        core = "".join(character for character in raw_token if character.isalpha())
        if len(core) >= 4 and core not in _SAFE_LABELS and core in serialized_output:
            raise CanonicalLayoutPrivacyError("source_text_leak_detected")
        digits = "".join(character for character in raw_token if character.isdigit())
        if len(digits) >= 4 and digits in output_digit_tokens:
            raise CanonicalLayoutPrivacyError("source_number_leak_detected")
    root = reader.trailer["/Root"]
    if any(name in root for name in ("/AcroForm", "/EmbeddedFiles", "/JavaScript")):
        raise CanonicalLayoutPrivacyError("active_content_detected")
    for page in reader.pages:
        if "/Annots" in page:
            raise CanonicalLayoutPrivacyError("annotation_detected")
        resources = page.get("/Resources") or {}
        if "/XObject" in resources:
            raise CanonicalLayoutPrivacyError("image_or_xobject_detected")

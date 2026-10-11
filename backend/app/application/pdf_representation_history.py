from __future__ import annotations

import json
import re

PDF_REPRESENTATIONS = frozenset(
    {"native_text", "vector_outlines", "raster_images", "mixed", "unknown_no_text"}
)
PDF_CREATION_METHODS = frozenset({"virtual_print", "direct_export", "scanner", "unknown"})
_EVIDENCE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:+-]{0,79}")


def prepare_pdf_representation_record(
    *,
    pdf_representation: str | None,
    pdf_creation_method: str | None,
    pdf_representation_confidence: float | None,
    pdf_classification_version: str | None,
    pdf_classification_evidence: list[str] | None,
) -> tuple[str | None, str | None, float | None, str | None, str | None]:
    representation = str(pdf_representation or "").strip().lower()
    if representation not in PDF_REPRESENTATIONS:
        representation = ""

    creation_method = str(pdf_creation_method or "").strip().lower()
    if creation_method not in PDF_CREATION_METHODS:
        creation_method = ""

    confidence = None
    if pdf_representation_confidence is not None:
        confidence = max(0.0, min(1.0, float(pdf_representation_confidence)))

    version = str(pdf_classification_version or "").strip()[:64] or None
    evidence_ids = sorted(
        {
            value
            for raw_value in (pdf_classification_evidence or [])
            if (value := str(raw_value or "").strip().lower())
            and _EVIDENCE_ID_PATTERN.fullmatch(value)
        }
    )[:20]
    evidence_json = json.dumps(evidence_ids, ensure_ascii=True) if evidence_ids else None
    return representation or None, creation_method or None, confidence, version, evidence_json

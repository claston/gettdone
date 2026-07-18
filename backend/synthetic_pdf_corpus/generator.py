from __future__ import annotations

from collections.abc import Callable

from synthetic_pdf_corpus.models import SUPPORTED_VARIANTS, SyntheticPdfScenario
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from synthetic_pdf_corpus.scanned_pdf import generate_scanned_pdf

PdfGenerator = Callable[[tuple[tuple[str, ...], ...]], bytes]

_GENERATORS: dict[str, PdfGenerator] = {
    "native_text": generate_native_text_pdf,
    "scanned": generate_scanned_pdf,
}


def generate_pdf(scenario: SyntheticPdfScenario, *, variant: str) -> bytes:
    if variant not in SUPPORTED_VARIANTS:
        raise ValueError(f"Unsupported synthetic PDF variant: {variant}.")
    if variant not in scenario.variants:
        raise ValueError(f"Variant {variant} is not enabled for scenario {scenario.scenario_id}.")
    return _GENERATORS[variant](scenario.pages)

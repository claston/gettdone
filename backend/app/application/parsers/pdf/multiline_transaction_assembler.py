from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.models import NormalizedTransaction
from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    has_amount_token_explicit_sign,
    parse_amount_token,
)
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.pdf_row_match_rules import (
    is_amount_only_row,
    is_date_only_row,
    match_tabular_date_prefix,
)
from app.application.normalization.pdf_signed_amount_rules import compute_tabular_signed_amount
from app.application.normalization.pdf_tabular_profile_rules import (
    find_profile_tabular_amount_tokens,
    profile_date_formats,
    should_ignore_profile_transaction_description,
)
from app.application.normalization.pdf_tabular_rules import select_tabular_amount_token
from app.application.normalization.pdf_text_rules import should_ignore_line, should_skip_transaction_description
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

_MAX_TRANSACTION_SPAN_LINES = 10
_STRUCTURAL_LABELS = {
    "DATA",
    "DATA MOV",
    "DESCRICAO",
    "HISTORICO",
    "DOCUMENTO",
    "NR DOC",
    "NR. DOC",
    "VALOR",
    "VALOR R$",
    "SALDO",
}
_TRANSACTION_EVIDENCE_HINTS = (
    "PIX",
    "TRANSFERENCIA",
    "PAGAMENTO",
    "COMPRA",
    "TARIFA",
    "SAQUE",
    "DEPOSITO",
    "RECEBIMENTO",
    "CREDITO",
    "DEBITO",
    "ESTORNO",
)


@dataclass(frozen=True, slots=True)
class _MultilineCandidate:
    raw_date: str
    description: str
    amount_token: AmountToken
    amount_role: str | None
    running_balance: float | None
    external_reference_id: str | None
    source_page: int
    source_line: int
    has_strong_evidence: bool


def parse_multiline_transaction_rows(
    lines: list[_PdfLine],
    layout_profile: DeclarativeLayoutProfile | None = None,
) -> tuple[list[_ParsedTransaction], int]:
    date_formats = profile_date_formats(layout_profile)
    fallback_year = _infer_fallback_year(lines, date_formats=date_formats)
    candidates_count = 0
    candidates: list[_MultilineCandidate] = []

    for start_index, line in enumerate(lines):
        date_start = _match_date_start(line.text, date_formats=date_formats)
        if date_start is None:
            continue
        candidates_count += 1
        raw_date, initial_description = date_start
        candidate = _assemble_candidate(
            lines=lines,
            start_index=start_index,
            raw_date=raw_date,
            initial_description=initial_description,
            date_formats=date_formats,
            layout_profile=layout_profile,
        )
        if candidate is not None:
            candidates.append(candidate)

    accept_weak_cohort = len(candidates) >= 2
    rows = [
        _build_parsed_row(candidate, fallback_year=fallback_year, date_formats=date_formats)
        for candidate in candidates
        if candidate.has_strong_evidence or accept_weak_cohort
    ]
    return rows, candidates_count


def _assemble_candidate(
    *,
    lines: list[_PdfLine],
    start_index: int,
    raw_date: str,
    initial_description: str,
    date_formats: tuple[str, ...],
    layout_profile: DeclarativeLayoutProfile | None,
) -> _MultilineCandidate | None:
    date_line = lines[start_index]
    description_parts = [initial_description] if initial_description else []
    external_reference_id: str | None = None
    amount_token: AmountToken | None = None
    amount_role: str | None = None
    running_balance: float | None = None

    stop_index = min(len(lines), start_index + _MAX_TRANSACTION_SPAN_LINES + 1)
    for index in range(start_index + 1, stop_index):
        current = lines[index]
        if current.page_number != date_line.page_number:
            break
        if _match_date_start(current.text, date_formats=date_formats) is not None:
            break

        normalized = normalize_upper_text(current.text)
        if _is_structural_noise(normalized):
            continue
        if should_skip_transaction_description(current.text) or should_ignore_profile_transaction_description(
            current.text,
            layout_profile,
        ):
            return None

        selected_amount = _select_amount_line(current.text, layout_profile=layout_profile)
        if selected_amount is not None:
            selected_token, selected_role, selected_balance = selected_amount
            if amount_token is None:
                amount_token = selected_token
                amount_role = selected_role
                if selected_balance is not None:
                    running_balance = parse_amount_token(selected_balance)
                    break
                continue
            running_balance = parse_amount_token(selected_token)
            break

        if amount_token is not None:
            break
        stripped = current.text.strip()
        if description_parts and external_reference_id is None and _is_document_reference(stripped):
            external_reference_id = stripped
            continue
        if stripped:
            description_parts.append(stripped)

    description = " ".join(part for part in description_parts if part).strip()
    if amount_token is None or not description or should_skip_transaction_description(description):
        return None

    normalized_description = normalize_upper_text(description)
    has_strong_evidence = (
        has_amount_token_explicit_sign(amount_token)
        or running_balance is not None
        or external_reference_id is not None
        or any(hint in normalized_description for hint in _TRANSACTION_EVIDENCE_HINTS)
    )
    return _MultilineCandidate(
        raw_date=raw_date,
        description=description,
        amount_token=amount_token,
        amount_role=amount_role,
        running_balance=running_balance,
        external_reference_id=external_reference_id,
        source_page=date_line.page_number,
        source_line=date_line.line_number,
        has_strong_evidence=has_strong_evidence,
    )


def _match_date_start(raw: str, *, date_formats: tuple[str, ...]) -> tuple[str, str] | None:
    stripped = raw.strip()
    if is_date_only_row(stripped, date_formats=date_formats):
        return stripped, ""
    match = match_tabular_date_prefix(stripped, date_formats=date_formats)
    if match is None:
        return None
    rest = match.group("rest").strip()
    if not rest or find_profile_tabular_amount_tokens(rest, None):
        return None
    return match.group("date"), rest


def _select_amount_line(
    raw: str,
    *,
    layout_profile: DeclarativeLayoutProfile | None,
) -> tuple[AmountToken, str | None, AmountToken | None] | None:
    tokens = find_profile_tabular_amount_tokens(raw, layout_profile)
    normalized = normalize_upper_text(raw)
    if not tokens or not (
        is_amount_only_row(raw.strip())
        or normalized.startswith("VALOR ")
        or normalized.startswith("R$ ")
        or normalized.startswith("US$ ")
    ):
        return None
    selected = select_tabular_amount_token(tokens, layout_profile=layout_profile)
    if selected is None:
        return None
    return selected.token, selected.role, selected.balance_token


def _build_parsed_row(
    candidate: _MultilineCandidate,
    *,
    fallback_year: int | None,
    date_formats: tuple[str, ...],
) -> _ParsedTransaction:
    raw_amount = parse_amount_token(candidate.amount_token)
    signed_amount = compute_tabular_signed_amount(
        raw_amount=raw_amount,
        role=candidate.amount_role,
        description=candidate.description,
    )
    return _ParsedTransaction(
        transaction=NormalizedTransaction(
            date=parse_row_date(
                candidate.raw_date,
                fallback_year=fallback_year,
                date_formats=date_formats,
            ),
            description=candidate.description,
            amount=signed_amount,
            type="inflow" if signed_amount >= 0 else "outflow",
        ),
        source_page=candidate.source_page,
        source_line=candidate.source_line,
        running_balance=candidate.running_balance,
        external_reference_id=candidate.external_reference_id,
        has_explicit_amount_sign=has_amount_token_explicit_sign(candidate.amount_token),
    )


def _infer_fallback_year(lines: list[_PdfLine], *, date_formats: tuple[str, ...]) -> int | None:
    for line in lines:
        date_start = _match_date_start(line.text, date_formats=date_formats)
        if date_start is None:
            continue
        raw_date = date_start[0]
        match = re.search(r"(?:^|\D)(20\d{2})(?:\D|$)", raw_date)
        if match:
            return int(match.group(1))
        compact_year = re.fullmatch(r"\d{4}(\d{2})", raw_date.strip())
        if compact_year:
            return 2000 + int(compact_year.group(1))
    return None


def _is_structural_noise(normalized: str) -> bool:
    if should_ignore_line(normalized):
        return True
    normalized_label = re.sub(r"[^A-Z0-9 ]", " ", normalized)
    normalized_label = re.sub(r"\s+", " ", normalized_label).strip()
    if normalized_label in _STRUCTURAL_LABELS:
        return True
    header_hits = sum(
        int(token in normalized_label)
        for token in ("DATA", "DESCRICAO", "HISTORICO", "DOCUMENTO", "VALOR", "SALDO")
    )
    return header_hits >= 2


def _is_document_reference(raw: str) -> bool:
    return bool(re.fullmatch(r"\d{4,20}", raw.strip()))

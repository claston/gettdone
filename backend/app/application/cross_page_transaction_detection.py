from __future__ import annotations

from dataclasses import dataclass

from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.normalization.pdf_row_match_rules import (
    is_amount_only_row,
    match_tabular_date_prefix,
)
from app.application.normalization.pdf_tabular_profile_rules import (
    find_profile_tabular_amount_tokens,
    profile_date_formats,
)
from app.application.normalization.text import normalize_upper_text

_TRANSACTION_HINTS = (
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
_BANK_STATEMENT_CONTEXT = (
    "CONTA CORRENTE",
    "CONTA BANCARIA",
    "AGENCIA",
    "LANCAMENTOS",
    "MOVIMENTACOES",
)
_BANK_NAMES = (
    "BANCO DO BRASIL",
    "BRADESCO",
    "CAIXA ECONOMICA",
    "ITAU",
    "SANTANDER",
    "NUBANK",
    "BANCO INTER",
    "SICREDI",
    "SISPRIME",
    "UNICRED",
    "CREDISIS",
    "CRESOL",
    "SICOOB",
)


@dataclass(frozen=True, slots=True)
class CrossPageTransactionSplitEvidence:
    split_kind: str
    description_page: int
    amount_page: int


@dataclass(frozen=True, slots=True)
class _PageSignals:
    lines: tuple[str, ...]
    date_description_indexes: tuple[int, ...]
    inline_transaction_indexes: tuple[int, ...]
    amount_only_indexes: tuple[int, ...]


def detect_cross_page_transaction_split(
    page_texts: list[str] | tuple[str, ...],
    *,
    layout_profile: DeclarativeLayoutProfile | None = None,
) -> CrossPageTransactionSplitEvidence | None:
    if len(page_texts) < 2 or not _looks_like_bank_statement(page_texts, layout_profile=layout_profile):
        return None

    page_signals = tuple(_extract_page_signals(text, layout_profile=layout_profile) for text in page_texts)
    for page_index in range(len(page_signals) - 1):
        left = page_signals[page_index]
        right = page_signals[page_index + 1]
        if _looks_like_tiled_columns(left, right):
            return CrossPageTransactionSplitEvidence(
                split_kind="tiled_columns",
                description_page=page_index + 1,
                amount_page=page_index + 2,
            )
        if _looks_like_page_boundary_split(left, right):
            return CrossPageTransactionSplitEvidence(
                split_kind="page_boundary",
                description_page=page_index + 1,
                amount_page=page_index + 2,
            )
    return None


def _extract_page_signals(
    page_text: str,
    *,
    layout_profile: DeclarativeLayoutProfile | None,
) -> _PageSignals:
    lines = tuple(" ".join(line.split()) for line in page_text.splitlines() if line.strip())
    date_description_indexes: list[int] = []
    inline_transaction_indexes: list[int] = []
    amount_only_indexes: list[int] = []
    date_formats = profile_date_formats(layout_profile)

    for index, line in enumerate(lines):
        date_match = match_tabular_date_prefix(line, date_formats=date_formats)
        amount_tokens = find_profile_tabular_amount_tokens(line, layout_profile)
        if date_match is not None:
            rest = date_match.group("rest").strip()
            if rest and not amount_tokens:
                date_description_indexes.append(index)
            elif rest and amount_tokens:
                inline_transaction_indexes.append(index)
        if is_amount_only_row(line):
            amount_only_indexes.append(index)

    return _PageSignals(
        lines=lines,
        date_description_indexes=tuple(date_description_indexes),
        inline_transaction_indexes=tuple(inline_transaction_indexes),
        amount_only_indexes=tuple(amount_only_indexes),
    )


def _looks_like_tiled_columns(description_page: _PageSignals, amount_page: _PageSignals) -> bool:
    description_count = len(description_page.date_description_indexes)
    amount_count = len(amount_page.amount_only_indexes)
    if description_count < 2 or amount_count < 2:
        return False
    if description_page.inline_transaction_indexes or amount_page.date_description_indexes:
        return False
    if description_page.amount_only_indexes or amount_page.inline_transaction_indexes:
        return False
    return description_count <= amount_count * 2 and amount_count <= (description_count * 2) + 1


def _looks_like_page_boundary_split(description_page: _PageSignals, amount_page: _PageSignals) -> bool:
    if not description_page.date_description_indexes or not amount_page.amount_only_indexes:
        return False
    description_index = description_page.date_description_indexes[-1]
    if description_index < max(0, len(description_page.lines) - 3):
        return False
    if any(index > description_index for index in description_page.amount_only_indexes):
        return False
    amount_index = amount_page.amount_only_indexes[0]
    if amount_index > 5:
        return False
    if any(
        index < amount_index
        for index in (*amount_page.date_description_indexes, *amount_page.inline_transaction_indexes)
    ):
        return False
    description = normalize_upper_text(description_page.lines[description_index])
    return any(hint in description for hint in _TRANSACTION_HINTS)


def _looks_like_bank_statement(
    page_texts: list[str] | tuple[str, ...],
    *,
    layout_profile: DeclarativeLayoutProfile | None,
) -> bool:
    if layout_profile is not None and str(layout_profile.bank or "").strip():
        return True
    normalized = normalize_upper_text("\n".join(page_texts))
    if "EXTRATO" not in normalized:
        return False
    has_context = any(marker in normalized for marker in _BANK_STATEMENT_CONTEXT)
    has_bank_name = any(marker in normalized for marker in _BANK_NAMES)
    return has_context and (has_bank_name or "SALDO" in normalized or "VALOR" in normalized)

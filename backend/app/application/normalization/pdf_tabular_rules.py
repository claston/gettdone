from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    has_amount_token_explicit_sign,
    parse_amount_token,
)
from app.application.normalization.text import normalize_upper_text


@dataclass(frozen=True)
class SelectedTabularAmount:
    token: AmountToken
    role: str | None
    description_end: int
    balance_token: AmountToken | None = None


def select_tabular_amount_token(
    tokens: list[AmountToken], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> SelectedTabularAmount | None:
    if not tokens:
        return None
    declarative_selection = select_declarative_tabular_amount(tokens, layout_profile)
    if declarative_selection is not None:
        return declarative_selection
    if len(tokens) == 1:
        return SelectedTabularAmount(token=tokens[0], role=None, description_end=tokens[0].start, balance_token=None)
    if len(tokens) >= 3:
        # OCR can split the running balance into two partial tokens; keep the first token as amount.
        return SelectedTabularAmount(token=tokens[0], role=None, description_end=tokens[0].start, balance_token=tokens[-1])
    # In statement-like tables with balance column, the rightmost amount is usually balance.
    return SelectedTabularAmount(token=tokens[-2], role=None, description_end=tokens[-2].start, balance_token=tokens[-1])


def select_declarative_tabular_amount(
    tokens: list[AmountToken], layout_profile: DeclarativeLayoutProfile | None
) -> SelectedTabularAmount | None:
    if layout_profile is None:
        return None

    amount_roles = tuple(role for role in layout_profile.expected_column_order if role in {"amount", "credit", "debit", "balance"})
    if not amount_roles:
        return None

    aligned_tokens = tokens[-len(amount_roles) :]
    aligned_roles = amount_roles[-len(aligned_tokens) :]
    role_tokens = list(zip(aligned_roles, aligned_tokens, strict=False))
    role_token_map = {role: token for role, token in role_tokens}
    transaction_role_tokens = [(role, token) for role, token in role_tokens if role != "balance"]
    if not transaction_role_tokens:
        return None

    description_end = tokens[0].start if {"credit", "debit"} & set(amount_roles) else transaction_role_tokens[0][1].start
    balance_token = role_token_map.get("balance")
    if {"credit", "debit", "balance"}.issubset(set(amount_roles)) and len(aligned_tokens) == 2 and balance_token is not None:
        transaction_token = aligned_tokens[0]
        preferred_role = next((role for role in amount_roles if role in {"credit", "debit"}), None)
        if has_amount_token_explicit_sign(transaction_token):
            preferred_role = "debit" if parse_amount_token(transaction_token) < 0 else "credit"
        return SelectedTabularAmount(
            token=transaction_token,
            role=preferred_role,
            description_end=description_end,
            balance_token=balance_token,
        )
    for preferred_role in ("debit", "credit", "amount"):
        for role, token in transaction_role_tokens:
            if role != preferred_role:
                continue
            amount = parse_amount_token(token)
            if preferred_role in {"credit", "debit"} and abs(amount) < 0.000001:
                continue
            return SelectedTabularAmount(
                token=token,
                role=token.role_hint or role,
                description_end=description_end,
                balance_token=balance_token,
            )

    role, token = transaction_role_tokens[0]
    return SelectedTabularAmount(
        token=token,
        role=token.role_hint or role,
        description_end=description_end,
        balance_token=balance_token,
    )


def split_spaced_grouped_amount_prefix(
    raw_amount_token: str,
    *,
    expected_amount: float,
) -> str | None:
    """Return a description prefix swallowed by an ambiguous space-grouped amount.

    A row such as ``M VALOR 3 349,00 D`` can mean description ``M VALOR 3``
    followed by ``349,00 D``. Only accept that interpretation when the remaining
    amount exactly matches the independently calculated running-balance delta.
    """

    match = re.fullmatch(
        r"\s*(?P<prefix>\d{1,3})[ \u00a0]+"
        r"(?P<amount>\d{1,3}(?:(?:\.|[ \u00a0])\d{3})*,\d{2}\s*[CD])\s*",
        raw_amount_token,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    recovered_amount = parse_amount_token(
        AmountToken(
            value=match.group("amount"),
            start=match.start("amount"),
            end=match.end("amount"),
        )
    )
    if abs(recovered_amount - expected_amount) > 0.02:
        return None
    return match.group("prefix")


def extract_document_reference(raw_description: str, *, layout_profile: DeclarativeLayoutProfile | None) -> str | None:
    if layout_profile is None:
        return None
    if "document" not in set(layout_profile.expected_column_order):
        return None
    parts = raw_description.split()
    if not parts:
        return None
    candidate = parts[-1].strip()
    if re.fullmatch(r"[A-Za-z0-9./_-]{3,}", candidate):
        return candidate
    return None


def has_declarative_table_header(lines: list[str], layout_profile: DeclarativeLayoutProfile | None) -> bool:
    if layout_profile is None or not layout_profile.expected_column_order or not layout_profile.column_aliases:
        return False

    required_roles = tuple(role for role in layout_profile.expected_column_order if role not in {"document", "balance"})
    if not required_roles:
        return False

    minimum_matches = min(3, len(required_roles))
    for line in lines:
        normalized_line = _normalize_text(line)
        matches = 0
        for role in required_roles:
            aliases = layout_profile.column_aliases.get(role, ())
            if any(_normalize_text(alias) in normalized_line for alias in aliases):
                matches += 1
        if matches >= minimum_matches:
            return True

    return False


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)

"""Numerical evidence for a balance warning; never a correction to financial data."""
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Literal


@dataclass(frozen=True)
class BalanceCheck:
    reference_balance: float
    credits_total: float
    debits_total: float
    expected_balance: float
    reported_balance: float
    difference: float
    movements_count: int
    order: Literal["ascending", "descending"] = "ascending"
    reference_date: str | None = None
    reference_page: int | None = None
    reference_line: int | None = None


def make_balance_check(*, reference_balance, movements, expected_balance, reported_balance,
                       descending=False, reference=None):
    return BalanceCheck(
        reference_balance=round(reference_balance, 2),
        credits_total=round(sum(value for value in movements if value > 0), 2),
        debits_total=round(-sum(value for value in movements if value < 0), 2),
        expected_balance=round(expected_balance, 2), reported_balance=round(reported_balance, 2),
        difference=round(reported_balance - expected_balance, 2), movements_count=len(movements),
        order="descending" if descending else "ascending",
        reference_date=getattr(reference, "date", None), reference_page=getattr(reference, "source_page", None),
        reference_line=getattr(reference, "source_line", None),
    )


def parse_balance_check(value) -> BalanceCheck | None:
    if not isinstance(value, dict):
        return None
    try:
        check = BalanceCheck(**value)
        numbers = (check.reference_balance, check.credits_total, check.debits_total,
                   check.expected_balance, check.reported_balance, check.difference)
        if (not all(isinstance(n, (int, float)) and not isinstance(n, bool) and isfinite(n) for n in numbers)
                or check.order not in {"ascending", "descending"}
                or not isinstance(check.movements_count, int) or check.movements_count < 0):
            return None
        return check
    except (TypeError, ValueError):
        return None


def statement_balance_check(opening, credits, outflows, closing, count) -> BalanceCheck | None:
    values = (opening, credits, outflows, closing)
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and isfinite(n) for n in values):
        return None
    expected = round(opening + credits - abs(outflows), 2)
    if round(closing - expected, 2) == 0:
        return None
    return BalanceCheck(round(opening, 2), round(credits, 2), round(abs(outflows), 2), expected,
                        round(closing, 2), round(closing - expected, 2), count)


def enrich_legacy_balance_checks(analysis: dict) -> dict:
    """Read-only explanation of older BTG snapshots in their accounting order.

    Other readers can have checkpoints/daily resets absent from old snapshots;
    do not invent numerical evidence for those readers. New conversions carry
    the actual validator's evidence for all supported layouts.
    """
    if analysis.get("layout_inference_name") != "btg_pactual_conta_corrente_pj_v1":
        return analysis
    rows = analysis.get("preview_transactions")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or row.get("is_deleted") for row in rows):
        return analysis
    from app.application.models import CanonicalTransaction
    from app.application.normalization.balance import annotate_balance_consistency

    canonical = []
    for row in rows:
        amount, balance, date = row.get("amount"), row.get("running_balance"), row.get("date")
        if (not isinstance(amount, (int, float)) or not isfinite(amount) or not isinstance(date, str)
                or balance is not None and (not isinstance(balance, (int, float)) or not isfinite(balance))):
            return analysis
        canonical.append(CanonicalTransaction(date, "", amount, "inflow" if amount >= 0 else "outflow",
            running_balance=balance, source_page=row.get("source_page"), source_line=row.get("source_line")))
    if any(a.date > b.date for a, b in zip(canonical, canonical[1:])):
        return analysis
    opening = analysis.get("opening_balance")
    checkpoints = {0: opening} if isinstance(opening, (int, float)) and not isinstance(opening, bool) and isfinite(opening) else None
    annotate_balance_consistency(canonical, balance_checkpoints=checkpoints)
    enriched = [{**row} for row in rows]
    for row, checked in zip(enriched, canonical, strict=True):
        if ("balance_consistency_failed" in (row.get("warning_types") or [])
                and not row.get("balance_check") and checked.balance_check is not None):
            row["balance_check"] = asdict(checked.balance_check)
    result = {**analysis, "preview_transactions": enriched}
    if not result.get("statement_balance_check") and any(
            "printed_summary_balance_mismatch" in (row.get("warning_types") or []) for row in rows):
        check = statement_balance_check(analysis.get("opening_balance"), analysis.get("total_inflows"),
            analysis.get("total_outflows"), analysis.get("closing_balance"), len(rows))
        if check:
            result["statement_balance_check"] = asdict(check)
    return result


def enrich_legacy_conversion_result(payload: dict) -> dict:
    analysis = payload.get("analysis")
    return {**payload, "analysis": enrich_legacy_balance_checks(analysis)} if isinstance(analysis, dict) else payload

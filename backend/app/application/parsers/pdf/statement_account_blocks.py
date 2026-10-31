"""Account scopes share provenance/continuity policies; readers opt in explicitly."""

import hashlib
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.parsers.pdf.statement_blocks import StatementBlock, _bounds, combine_statement_results
from app.application.parsers.pdf.verified_statements import fail
from app.application.pdf_document_segmentation import _has_statement_rows


@dataclass(frozen=True)
class AccountHeader:
    account: dict[str, str]
    period: tuple[str, str]


def account_block_plan(pages, identify_header):
    headers = [(index, header) for index, text in enumerate(pages) if (header := identify_header(text))]
    if len({header.account["account_id"] for _, header in headers}) < 2:
        return []
    # A cover may precede the first identified account, but an unidentified cash
    # ledger may not be silently assigned to that account.
    if any(_has_statement_rows("\n".join(text.splitlines()[index:]))
           for text in pages[:headers[0][0]] for index in range(len(text.splitlines()))):
        fail("Há movimentos antes da primeira conta identificada no documento.")
    return [(StatementBlock(0 if index == 0 else start,
                            headers[index+1][0] if index+1 < len(headers) else len(pages), header.period), header.account)
            for index, (start, header) in enumerate(headers)]


def account_scope(*, bank_code, bank_branch, account_number, holder_name):
    identity = "|".join((bank_code, bank_branch, account_number))
    return {"account_id": "account_" + hashlib.sha256(identity.encode()).hexdigest()[:16],
            "bank_code": bank_code, "bank_branch": bank_branch, "account_number": account_number, "holder_name": holder_name}


def combine_account_results(results, plan, pages, *, original_extraction=None, document_hash=""):
    blocks = [block for block, _ in plan]
    combined = combine_statement_results(results, blocks, pages, original_extraction=original_extraction, document_hash=document_hash)
    canonical, parts, previous, continuity_failed = [], [], {}, False
    offset = 0
    all_valid = True
    for index, ((block, account), result) in enumerate(zip(plan, results, strict=True), 1):
        metrics = result.parse_metrics
        bounds = _bounds(result)
        opening, closing = (tuple(float(value) for value in bounds) if bounds else (None, None))
        validated = bool(metrics.get("financial_controls_validated")) and not metrics.get("statement_validation_failed")
        all_valid &= validated
        scope = {**account, "block_id": f"statement-account-{index}", "period_start": block.period[0],
                 "period_end": block.period[1], "opening_balance": opening, "closing_balance": closing,
                 "controls_validated": validated}
        prior = previous.get(account["account_id"])
        contiguous = prior is None or (
            date.fromisoformat(block.period[0]) == date.fromisoformat(prior["period_end"]) + timedelta(days=1)
            and prior["closing_balance"] is not None and opening is not None
            and Decimal(str(prior["closing_balance"])) == Decimal(str(opening)))
        continuity_failed |= not contiguous
        previous[account["account_id"]] = scope
        count = len(result.canonical_transactions or [])
        for row in combined.canonical_transactions[offset:offset+count]:
            warnings = [w for w in row.warnings if w != "statement_blocks_not_contiguous"]
            canonical.append(replace(row, statement_account=dict(scope), warnings=warnings))
        offset += count
        parts.append({"id": scope["block_id"], "document_type": "bank_statement", "included_in_statement": True,
                      "pages": list(range(block.start+1, block.stop+1)), "statement_account": dict(scope),
                      "period": {"start_date": block.period[0], "end_date": block.period[1], "basis": "document_metadata"},
                      "opening_balance": opening, "closing_balance": closing,
                      "requires_review": not validated or not contiguous or result.parse_metrics.get("partial_statement") == 1})
    if offset != len(combined.canonical_transactions or []):
        fail("A associação dos lançamentos às contas está incompleta.")
    metrics = dict(combined.parse_metrics)
    metrics.update(statement_accounts_count=len(previous), financial_controls_validated=int(all_valid),
                   balance_bounds_unavailable=1, balance_validation_status="validated_per_account" if all_valid else "review_required")
    review = continuity_failed or not all_valid or any(
        r.parse_metrics.get("export_recommendation") == "review_recommended" for r in results)
    if review:
        reason = "statement_account_blocks_not_contiguous" if continuity_failed else "account_controls_require_review"
        metrics.update(export_recommendation="review_recommended", export_recommendation_reason=reason)
        if canonical:
            canonical[0] = replace(canonical[0], warnings=list(dict.fromkeys([
                *canonical[0].warnings, "manual_review_recommended", reason])))
    else:
        metrics.update(export_recommendation="safe_to_export", export_recommendation_reason="all_accounts_controls_validated",
                       confidence_band="high")
        canonical = [replace(row, warnings=[w for w in row.warnings if w != "manual_review_recommended"]) for row in canonical]
    metrics.update(build_canonical_quality_metrics(canonical))
    return replace(combined, canonical_transactions=canonical, document_parts=parts, parse_metrics=metrics)

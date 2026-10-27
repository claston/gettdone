"""Read-only statement coverage and readiness; never certifies accounting/fiscal results."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def money(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def iso_date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def normalized(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def coverage(start, end, intervals, has_documents):
    spans = sorted((max(start, a).toordinal(), min(end, b).toordinal()) for a, b in intervals if b >= start and a <= end)
    gaps, overlaps, cursor = [], [], start.toordinal()
    for a, b in spans:
        if a > cursor:
            gaps.append({"start_date": date.fromordinal(cursor).isoformat(), "end_date": date.fromordinal(a - 1).isoformat()})
        if a < cursor:
            overlaps.append({"start_date": date.fromordinal(a).isoformat(), "end_date": date.fromordinal(min(b, cursor - 1)).isoformat()})
        cursor = max(cursor, b + 1)
    if cursor <= end.toordinal():
        gaps.append({"start_date": date.fromordinal(cursor).isoformat(), "end_date": end.isoformat()})
    status = (
        "missing"
        if not has_documents
        else "indeterminate"
        if not spans
        else "overlapping"
        if overlaps
        else "partial"
        if gaps
        else "complete"
    )
    return {"status": status, "gaps": gaps, "overlaps": overlaps}


def assess_work(client, period, accounts, documents, settings):
    start, end = iso_date(period["start_date"]), iso_date(period["end_date"])
    blockers, reservations, movements, groups, account_coverage = [], [], [], [], []
    scoped = {a["id"]: a for a in accounts if a["id"] in settings.get("account_ids", [])}
    evidence, exclusions = settings.get("evidence", {}), settings.get("exclusions", {})
    intervals, account_docs, signatures = defaultdict(list), defaultdict(list), defaultdict(list)
    valid_documents = 0

    def issue(target, code, message, account_id=None, job_id=None):
        target.append({"code": code, "message": message, "account_id": account_id, "job_id": job_id})

    if not settings.get("scope_confirmed_at") or not scoped:
        issue(blockers, "scope_unconfirmed", "Confirme ao menos uma conta no escopo do trabalho.")
    for doc in documents:
        job, account_id = doc["job_id"], doc.get("account_id")
        if job in exclusions:
            continue
        if not account_id:
            issue(blockers, "account_unassigned", "Identifique a conta de origem do extrato.", job_id=job)
            continue
        if account_id not in scoped:
            continue  # An account explicitly outside this work creates no coverage obligation.
        account = scoped[account_id]
        if doc.get("expired") or doc["status"] != "completed":
            issue(
                blockers,
                "document_unavailable",
                "Extrato indisponível, em processamento ou com falha. Corrija ou exclua do escopo.",
                account_id,
                job,
            )
            continue
        analysis, source, confirmed = doc.get("analysis", {}), doc.get("source", {}), evidence.get(job, {})
        if analysis.get("financial_document"):
            issue(blockers, "not_statement", "Este relatório não é um extrato bancário de movimentações.", account_id, job)
            continue
        account_docs[account_id].append(doc)
        if not doc.get("reviewed_at"):
            issue(blockers, "review_pending", "Revise e confirme o extrato no workspace.", account_id, job)
        identity_conflict = bool(
            source.get("holder_tax_id") and client.get("tax_id") and normalized(source["holder_tax_id"]) != normalized(client["tax_id"])
        )
        for source_field, account_field in (("bank_code", "bank_code"), ("branch", "branch"), ("account_number", "account_number")):
            a, b = normalized(source.get(source_field)), normalized(account.get(account_field))
            identity_conflict |= bool(a and b and a != b)
        if identity_conflict:
            issue(
                blockers,
                "identity_conflict",
                "Titular ou conta de origem conflita com o cadastro. Corrija o vínculo ou exclua o documento.",
                account_id,
                job,
            )
        elif not confirmed.get("identity_confirmed"):
            issue(blockers, "identity_unconfirmed", "Confirme o titular e a conta de origem com o documento.", account_id, job)
        documentary = source.get("period") or {}
        a, b = iso_date(confirmed.get("start_date")), iso_date(confirmed.get("end_date"))
        if not (a and b) and documentary.get("document_coverage"):
            a, b = iso_date(documentary.get("start_date")), iso_date(documentary.get("end_date"))
        if a and b and a <= b:
            intervals[account_id].append((a, b))
        else:
            issue(reservations, "coverage_indeterminate", "O intervalo documental deste extrato não foi confirmado.", account_id, job)
        rows = [row for row in analysis.get("preview_transactions", []) if not row.get("is_deleted")]
        if (a and b and a <= end and b >= start) or any(iso_date(r.get("date")) and start <= iso_date(r["date"]) <= end for r in rows):
            valid_documents += 1
        if not rows:
            opening, closing = money(analysis.get("opening_balance")), money(analysis.get("closing_balance"))
            balance = money(confirmed.get("balance"))
            if not confirmed.get("empty_confirmed") or (opening is None or closing is None) and balance is None:
                issue(
                    blockers, "empty_unconfirmed", "Confirme ausência de movimentos e saldo do extrato sem movimentação.", account_id, job
                )
        financial_codes = {
            "balance_consistency_failed",
            "financial_validation_failed",
            "printed_summary_balance_mismatch",
            "partial_statement",
        }
        check = analysis.get("statement_balance_check") or {}
        financial_conflict = bool(check and money(check.get("difference")) != Decimal(0))
        if not rows and money(confirmed.get("balance")) is not None:
            reported = money(confirmed["balance"])
            financial_conflict |= any(
                money(analysis.get(k)) is not None and money(analysis[k]) != reported for k in ("opening_balance", "closing_balance")
            )
        metrics = analysis.get("pdf_processing_metrics") or {}
        financial_conflict |= bool(metrics.get("partial_statement", 0))
        financial_conflict |= bool(metrics.get("balance_consistency_failed_count", 0))
        if analysis.get("transactions_total", len(rows)) > len(analysis.get("preview_transactions", [])):
            issue(blockers, "incomplete_rows", "O resultado não contém todos os movimentos para este fechamento.", account_id, job)
        for index, row in enumerate(rows):
            financial_conflict |= bool(financial_codes.intersection(row.get("warning_types") or []))
            row_check = row.get("balance_check") or {}
            financial_conflict |= bool(row_check and money(row_check.get("difference")) != Decimal(0))
            day, amount = iso_date(row.get("date")), money(row.get("amount"))
            if day is None or amount is None or abs(amount) > Decimal("1e16"):
                issue(
                    blockers,
                    "invalid_movement",
                    "Corrija lançamentos sem data ou valor válido antes de recortar o período.",
                    account_id,
                    job,
                )
                continue
            if not start <= day <= end:
                continue
            row_id = str(row.get("transaction_id") or f"row-{index}")
            item = {
                "job_id": job,
                "transaction_id": row_id,
                "account_id": account_id,
                "date": day.isoformat(),
                "description": str(row.get("description") or ""),
                "amount": float(amount),
                "source_sequence": index,
            }
            movements.append(item)
            signature = (
                account_id,
                day.isoformat(),
                str(amount.quantize(Decimal("0.01"))),
                " ".join(item["description"].casefold().split()),
            )
            signatures[signature].append(item)
        opening, closing = money(analysis.get("opening_balance")), money(analysis.get("closing_balance"))
        amounts = [money(row.get("amount")) for row in rows]
        if opening is not None and closing is not None and all(value is not None for value in amounts):
            financial_conflict |= abs(opening + sum(amounts, Decimal(0)) - closing) >= Decimal("0.01")
        if financial_conflict:
            issue(
                blockers,
                "financial_conflict",
                "Há inconsistência financeira no extrato; a confirmação de revisão não elimina esse bloqueio.",
                account_id,
                job,
            )
    for signature, candidates in signatures.items():
        if len(candidates) < 2:
            continue
        group_id = fingerprint([signature, sorted((c["job_id"], c["transaction_id"], c["source_sequence"]) for c in candidates)])
        resolved = bool((settings.get("duplicate_resolutions") or {}).get(group_id))
        groups.append({"id": group_id, "resolved": resolved, "items": candidates})
        if not resolved:
            issue(
                blockers,
                "possible_duplicate",
                "Possível duplicidade: exclua o documento afetado ou justifique movimentos distintos após conferência.",
                signature[0],
            )
    for account_id in scoped:
        result = coverage(start, end, intervals[account_id], bool(account_docs[account_id]))
        if len(intervals[account_id]) < len(account_docs[account_id]) and result["status"] == "complete":
            result["status"] = "indeterminate"
        result.update(account_id=account_id, documents_count=len(account_docs[account_id]))
        account_coverage.append(result)
        if result["status"] != "complete":
            issue(reservations, f"coverage_{result['status']}", "Cobertura documental exige ressalva ou complementação.", account_id)
        ordered = sorted(
            account_docs[account_id],
            key=lambda doc: (
                evidence.get(doc["job_id"], {}).get("start_date") or (doc.get("source", {}).get("period") or {}).get("start_date") or "",
                doc["job_id"],
            ),
        )
        for previous, current in zip(ordered, ordered[1:]):
            before = money(previous.get("analysis", {}).get("closing_balance"))
            after = money(current.get("analysis", {}).get("opening_balance"))
            if result["overlaps"] or before is None or after is None:
                issue(reservations, "continuity_unverified", "Continuidade dos saldos entre extratos não verificável.", account_id)
            elif before != after:
                issue(blockers, "balance_discontinuity", "O saldo final de um extrato diverge do saldo inicial do seguinte.", account_id)
    if not valid_documents:
        issue(blockers, "no_valid_statement", "Inclua ao menos um extrato válido antes de concluir.")
    return {
        "coverage": account_coverage,
        "blockers": blockers,
        "reservations": reservations,
        "movements": movements,
        "duplicate_groups": groups,
        "documents_count": valid_documents,
        "accounts_count": len(scoped),
        "movements_count": len(movements),
        "can_complete": not blockers,
        "readiness": "blocked" if blockers else "with_reservations" if reservations else "ready",
    }

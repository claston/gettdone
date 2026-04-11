from app.application.ledger_match_models import LedgerProblemInsight, LedgerReconciliationRow


def generate_reconciliation_problems(rows: list[LedgerReconciliationRow]) -> list[LedgerProblemInsight]:
    missing_payment_count = _count_missing_payments(rows)
    missing_receipt_count = _count_missing_receipts(rows)
    amount_mismatch_count = _count_divergent_pairs(rows, reason="amount_mismatch")
    duplicate_group_count = _count_possible_duplicate_groups(rows)

    problems: list[LedgerProblemInsight] = []
    if missing_payment_count > 0:
        problems.append(
            LedgerProblemInsight(
                type="missing_payment",
                title="Pagamento nao encontrado no banco",
                description=(
                    f"{missing_payment_count} pagamentos da planilha nao foram encontrados no extrato."
                ),
            )
        )

    if missing_receipt_count > 0:
        problems.append(
            LedgerProblemInsight(
                type="missing_receipt",
                title="Recebimento nao registrado internamente",
                description=(
                    f"{missing_receipt_count} recebimentos do extrato nao foram encontrados na planilha."
                ),
            )
        )

    if amount_mismatch_count > 0:
        problems.append(
            LedgerProblemInsight(
                type="amount_mismatch",
                title="Valor divergente entre extrato e planilha",
                description=f"{amount_mismatch_count} pares apresentam diferenca de valor.",
            )
        )

    if duplicate_group_count > 0:
        problems.append(
            LedgerProblemInsight(
                type="possible_duplicate",
                title="Possivel duplicidade detectada",
                description=f"{duplicate_group_count} grupos com possivel duplicidade foram encontrados.",
            )
        )

    return problems


def _count_missing_payments(rows: list[LedgerReconciliationRow]) -> int:
    return sum(
        1
        for row in rows
        if row.source == "sheet"
        and row.status == "pendente"
        and row.reason == "missing_in_bank"
        and row.amount < 0
    )


def _count_missing_receipts(rows: list[LedgerReconciliationRow]) -> int:
    return sum(
        1
        for row in rows
        if row.source == "bank"
        and row.status == "pendente"
        and row.reason == "missing_in_sheet"
        and row.amount > 0
    )


def _count_divergent_pairs(rows: list[LedgerReconciliationRow], reason: str) -> int:
    pair_ids: set[tuple[str, str]] = set()
    for row in rows:
        if row.status != "divergente" or row.reason != reason or row.matched_row_id is None:
            continue
        pair_ids.add(tuple(sorted((row.row_id, row.matched_row_id))))
    return len(pair_ids)


def _count_possible_duplicate_groups(rows: list[LedgerReconciliationRow]) -> int:
    groups: dict[tuple[str, str, str, float], int] = {}
    for row in rows:
        if row.status != "pendente":
            continue
        key = (row.source, row.date, row.description, round(row.amount, 2))
        groups[key] = groups.get(key, 0) + 1
    return sum(1 for count in groups.values() if count > 1)

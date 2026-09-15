from app.application.models import CanonicalTransaction

_DESCENDING_RUNNING_BALANCE_LAYOUTS = {
    "stone_extrato_conta_corrente_a4_v1",
    "santander_aplicativo_empresas_conta_corrente_extrato_v1",
    "santander_internet_banking_empresarial_movimentacao_a4_data_historico_valor_v1",
}


def uses_descending_running_balance(layout_name: str | None) -> bool:
    return str(layout_name or "").strip().lower() in _DESCENDING_RUNNING_BALANCE_LAYOUTS


def annotate_balance_consistency(canonical_transactions: list[CanonicalTransaction]) -> tuple[int, int]:
    checked_count = 0
    failed_count = 0
    previous_balance_row: CanonicalTransaction | None = None
    amounts_since_balance = 0.0
    tolerance = 0.01

    for current in canonical_transactions:
        if previous_balance_row is None:
            if current.running_balance is not None:
                previous_balance_row = current
                amounts_since_balance = current.amount if uses_descending_running_balance(current.layout_name) else 0.0
            continue

        descending = uses_descending_running_balance(current.layout_name or previous_balance_row.layout_name)
        if descending:
            if current.running_balance is None:
                amounts_since_balance += current.amount
                continue
            expected_current_balance = previous_balance_row.running_balance - amounts_since_balance
        else:
            amounts_since_balance += current.amount
            if current.running_balance is None:
                continue
            expected_current_balance = previous_balance_row.running_balance + amounts_since_balance

        checked_count += 1
        if abs(current.running_balance - expected_current_balance) > tolerance:
            failed_count += 1
            if "balance_consistency_failed" not in current.warnings:
                current.warnings.append("balance_consistency_failed")
        previous_balance_row = current
        amounts_since_balance = current.amount if descending else 0.0

    return checked_count, failed_count

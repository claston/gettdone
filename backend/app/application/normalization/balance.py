from typing import Protocol, Sequence

from app.application.models import CanonicalTransaction

_DESCENDING_RUNNING_BALANCE_LAYOUTS = {
    "banco_pan_extrato_conta_pix_saldo_v1",
    "stone_extrato_conta_corrente_a4_v1",
    "santander_aplicativo_empresas_conta_corrente_extrato_v1",
    "santander_internet_banking_empresarial_movimentacao_a4_data_historico_valor_v1",
}
_DYNAMIC_RUNNING_BALANCE_ORDER_LAYOUTS = {
    "stone_extrato_conta_corrente_a4_v1",
}


class _BalanceRow(Protocol):
    amount: float
    running_balance: float | None


def resolve_descending_running_balance(
    rows: Sequence[_BalanceRow],
    *,
    layout_name: str | None = None,
) -> bool:
    resolved_layout_name = str(layout_name or "").strip().lower()
    if not resolved_layout_name:
        resolved_layout_name = next(
            (
                str(getattr(row, "layout_name", "") or "").strip().lower()
                for row in rows
                if getattr(row, "layout_name", None)
            ),
            "",
        )
    default_descending = uses_descending_running_balance(resolved_layout_name)
    if resolved_layout_name not in _DYNAMIC_RUNNING_BALANCE_ORDER_LAYOUTS:
        return default_descending

    ascending_failures, descending_failures, checked_count = _score_running_balance_orders(rows)
    if checked_count == 0 or ascending_failures == descending_failures:
        return default_descending
    return descending_failures < ascending_failures


def _score_running_balance_orders(rows: Sequence[_BalanceRow]) -> tuple[int, int, int]:
    previous_running_balance: float | None = None
    ascending_amounts = 0.0
    descending_amounts = 0.0
    ascending_failures = 0
    descending_failures = 0
    checked_count = 0
    tolerance = 0.01

    for row in rows:
        amount = float(row.amount)
        running_balance = row.running_balance
        if previous_running_balance is None:
            if running_balance is not None:
                previous_running_balance = float(running_balance)
                descending_amounts = amount
            continue

        ascending_amounts += amount
        if running_balance is None:
            descending_amounts += amount
            continue

        current_running_balance = float(running_balance)
        ascending_expected = previous_running_balance + ascending_amounts
        descending_expected = previous_running_balance - descending_amounts
        ascending_failures += abs(current_running_balance - ascending_expected) > tolerance
        descending_failures += abs(current_running_balance - descending_expected) > tolerance
        checked_count += 1
        previous_running_balance = current_running_balance
        ascending_amounts = 0.0
        descending_amounts = amount

    return ascending_failures, descending_failures, checked_count


def uses_descending_running_balance(layout_name: str | None) -> bool:
    return str(layout_name or "").strip().lower() in _DESCENDING_RUNNING_BALANCE_LAYOUTS


def annotate_balance_consistency(
    canonical_transactions: list[CanonicalTransaction],
    *,
    balance_checkpoints: dict[int, float] | None = None,
) -> tuple[int, int]:
    checked_count = 0
    failed_count = 0
    previous_balance_row: CanonicalTransaction | None = None
    amounts_since_balance = 0.0
    tolerance = 0.01
    descending = resolve_descending_running_balance(canonical_transactions)

    for index, current in enumerate(canonical_transactions):
        checkpoint = (balance_checkpoints or {}).get(index)
        if checkpoint is not None:
            if current.running_balance is None:
                previous_balance_row = CanonicalTransaction(
                    date=current.date,
                    description="SALDO ANTERIOR",
                    amount=0.0,
                    type="inflow",
                    layout_name=current.layout_name,
                    running_balance=checkpoint,
                )
                amounts_since_balance = current.amount
                continue

            checked_count += 1
            expected_current_balance = checkpoint + current.amount
            if abs(current.running_balance - expected_current_balance) > tolerance:
                failed_count += 1
                if "balance_consistency_failed" not in current.warnings:
                    current.warnings.append("balance_consistency_failed")
            previous_balance_row = current
            amounts_since_balance = current.amount if descending else 0.0
            continue

        if previous_balance_row is None:
            if current.running_balance is not None:
                previous_balance_row = current
                amounts_since_balance = current.amount if descending else 0.0
            continue

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

from app.application.models import CanonicalTransaction


def annotate_balance_consistency(canonical_transactions: list[CanonicalTransaction]) -> tuple[int, int]:
    checked_count = 0
    failed_count = 0
    previous: CanonicalTransaction | None = None
    tolerance = 0.01

    for current in canonical_transactions:
        if current.running_balance is None:
            previous = current
            continue
        if previous is None or previous.running_balance is None:
            previous = current
            continue

        expected_current_balance = previous.running_balance + current.amount
        checked_count += 1
        if abs(current.running_balance - expected_current_balance) > tolerance:
            failed_count += 1
            if "balance_consistency_failed" not in current.warnings:
                current.warnings.append("balance_consistency_failed")
        previous = current

    return checked_count, failed_count

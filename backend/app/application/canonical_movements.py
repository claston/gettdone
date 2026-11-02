"""Contracts for the opt-in, retained-lifetime canonical bank movement store."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class CanonicalRollout:
    enabled: bool = False
    organization_ids: frozenset[str] = field(default_factory=frozenset)

    def allows(self, *, organization_id: str, product: str, new_analysis: bool) -> bool:
        return self.enabled and new_analysis and product == "pro" and organization_id in self.organization_ids


@dataclass(frozen=True)
class MovementScope:
    """Server-resolved authenticated owner and original document scope, never request claims."""

    actor_id: str
    organization_id: str
    job_id: str
    analysis_id: str
    client_id: str | None = None


def exact_amount(value) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError("Invalid monetary amount")
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) >= Decimal("10000000000000000"):
            raise ValueError("Invalid monetary amount")
        cents = amount.quantize(Decimal("0.01"))
        if cents != amount:
            raise ValueError("Monetary amount must have at most two decimal places")
        # Existing exporters accept floats. Reject values that cannot round-trip
        # through that compatibility boundary instead of changing cents silently.
        if Decimal(str(float(cents))) != cents:
            raise ValueError("Amount exceeds exact legacy export compatibility")
        return cents
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid monetary amount") from exc

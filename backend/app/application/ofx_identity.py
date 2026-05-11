import hashlib
import hmac
import os
from collections import defaultdict
from collections.abc import Iterable

from app.application.models import NormalizedTransaction
from app.application.normalization.text import normalize_description_text

_DEFAULT_DEV_SECRET = "ofxsimples-development-fitid-secret"


def build_fit_id_sequence(transactions: Iterable[NormalizedTransaction]) -> list[str]:
    occurrences: dict[tuple[str, str, str], int] = defaultdict(int)
    fit_ids: list[str] = []

    for transaction in transactions:
        key = _transaction_key(transaction)
        occurrences[key] += 1
        fit_ids.append(build_stable_fit_id(transaction, occurrence=occurrences[key]))

    return fit_ids


def build_stable_fit_id(transaction: NormalizedTransaction, *, occurrence: int = 1) -> str:
    payload = "|".join(
        [
            str(transaction.date).strip(),
            f"{float(transaction.amount):.2f}",
            normalize_description_text(transaction.description),
            str(max(1, occurrence)),
        ]
    )
    digest = hmac.new(_fitid_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"OFXS-{digest[:16].upper()}"


def _transaction_key(transaction: NormalizedTransaction) -> tuple[str, str, str]:
    return (
        str(transaction.date).strip(),
        f"{float(transaction.amount):.2f}",
        normalize_description_text(transaction.description),
    )


def _fitid_secret() -> str:
    return (
        os.getenv("OFX_FITID_SECRET", "").strip()
        or os.getenv("ACCESS_CONTROL_TOKEN_SECRET", "").strip()
        or _DEFAULT_DEV_SECRET
    )


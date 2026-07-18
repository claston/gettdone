from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from synthetic_pdf_corpus.models import (
    SUPPORTED_VARIANTS,
    ExpectedTransaction,
    ScenarioExpectations,
    SyntheticPdfScenario,
)


def load_scenarios(directory: Path) -> tuple[SyntheticPdfScenario, ...]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"No synthetic PDF scenarios found in {directory}.")
    scenarios = tuple(load_scenario(path) for path in paths)
    scenario_ids = [scenario.scenario_id for scenario in scenarios]
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError(f"Duplicate synthetic PDF scenario ids in {directory}.")
    return scenarios


def load_scenario(path: Path) -> SyntheticPdfScenario:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load synthetic PDF scenario {path}.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Synthetic PDF scenario must be an object: {path}.")

    schema_version = _required_int(payload, "schema_version", path=path)
    if schema_version != 1:
        raise ValueError(f"Unsupported synthetic PDF scenario schema_version={schema_version}: {path}.")

    scenario_id = _required_string(payload, "scenario_id", path=path)
    description = _required_string(payload, "description", path=path)
    pages = _load_pages(payload.get("pages"), path=path)
    variants = _load_variants(payload.get("variants"), path=path)
    expected = _load_expectations(payload.get("expected"), path=path)
    return SyntheticPdfScenario(
        schema_version=schema_version,
        scenario_id=scenario_id,
        description=description,
        pages=pages,
        variants=variants,
        expected=expected,
    )


def _load_pages(raw: Any, *, path: Path) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Synthetic PDF scenario pages must be a non-empty list: {path}.")
    pages: list[tuple[str, ...]] = []
    for raw_page in raw:
        if not isinstance(raw_page, list) or not raw_page:
            raise ValueError(f"Each synthetic PDF scenario page must be a non-empty list: {path}.")
        lines = tuple(str(line).strip() for line in raw_page if str(line).strip())
        if not lines:
            raise ValueError(f"Synthetic PDF scenario page has no usable lines: {path}.")
        pages.append(lines)
    return tuple(pages)


def _load_variants(raw: Any, *, path: Path) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Synthetic PDF scenario variants must be a non-empty list: {path}.")
    variants = tuple(str(item).strip() for item in raw if str(item).strip())
    unsupported = sorted(set(variants) - SUPPORTED_VARIANTS)
    if unsupported:
        raise ValueError(f"Unsupported synthetic PDF variants {unsupported}: {path}.")
    return variants


def _load_expectations(raw: Any, *, path: Path) -> ScenarioExpectations:
    if not isinstance(raw, dict):
        raise ValueError(f"Synthetic PDF scenario expected must be an object: {path}.")
    raw_transactions = raw.get("transactions")
    if not isinstance(raw_transactions, list) or not raw_transactions:
        raise ValueError(f"Synthetic PDF scenario transactions must be a non-empty list: {path}.")
    transactions = tuple(_load_expected_transaction(item, path=path) for item in raw_transactions)
    max_false_positives = int(raw.get("max_false_positives", 0))
    if max_false_positives < 0:
        raise ValueError(f"max_false_positives cannot be negative: {path}.")
    return ScenarioExpectations(
        transactions=transactions,
        max_false_positives=max_false_positives,
        selected_parser=str(raw.get("selected_parser", "")).strip(),
        enforce_success=bool(raw.get("enforce_success", True)),
        known_gap=str(raw.get("known_gap", "")).strip(),
    )


def _load_expected_transaction(raw: Any, *, path: Path) -> ExpectedTransaction:
    if not isinstance(raw, dict):
        raise ValueError(f"Expected transaction must be an object: {path}.")
    transaction_type = _required_string(raw, "type", path=path)
    if transaction_type not in {"inflow", "outflow"}:
        raise ValueError(f"Expected transaction type must be inflow or outflow: {path}.")
    return ExpectedTransaction(
        date=_required_string(raw, "date", path=path),
        description_contains=_required_string(raw, "description_contains", path=path),
        amount=float(raw["amount"]),
        transaction_type=transaction_type,
    )


def _required_string(payload: dict[str, Any], key: str, *, path: Path) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"Synthetic PDF scenario requires {key}: {path}.")
    return value


def _required_int(payload: dict[str, Any], key: str, *, path: Path) -> int:
    try:
        return int(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Synthetic PDF scenario requires integer {key}: {path}.") from exc

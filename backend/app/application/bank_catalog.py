from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.application.normalization.text import normalize_upper_text

_BANKS_FILE = Path(__file__).resolve().parent.parent / "data" / "banks_br.json"


@dataclass(frozen=True)
class BankRecord:
    code: str
    name: str
    short_name: str
    aliases: tuple[str, ...]
    active: bool


@lru_cache(maxsize=1)
def load_bank_catalog() -> tuple[BankRecord, ...]:
    try:
        payload = json.loads(_BANKS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()

    rows = payload.get("banks")
    if not isinstance(rows, list):
        return ()

    records: list[BankRecord] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        code = _normalize_code(item.get("compe"))
        if not code:
            continue
        aliases_raw = item.get("aliases")
        aliases = tuple(str(value).strip() for value in aliases_raw if str(value).strip()) if isinstance(aliases_raw, list) else ()
        records.append(
            BankRecord(
                code=code,
                name=str(item.get("name") or "").strip(),
                short_name=str(item.get("short_name") or "").strip(),
                aliases=aliases,
                active=bool(item.get("active", True)),
            )
        )
    return tuple(records)


def resolve_bank_code_from_name(bank_name: str | None) -> str | None:
    normalized_target = normalize_upper_text(bank_name or "")
    if not normalized_target:
        return None

    for record in load_bank_catalog():
        candidates = (record.name, record.short_name, *record.aliases)
        normalized_candidates = {normalize_upper_text(value) for value in candidates if value}
        if normalized_target in normalized_candidates:
            return record.code
    return None


def normalize_bank_code(value: str | None) -> str | None:
    return _normalize_code(value)


def list_bank_options(*, include_inactive: bool = False) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for record in load_bank_catalog():
        if not include_inactive and not record.active:
            continue
        label_name = record.short_name or record.name or "Banco"
        items.append(
            {
                "code": record.code,
                "name": record.name,
                "short_name": record.short_name,
                "label": f"{label_name} ({record.code})",
                "aliases": list(record.aliases),
            }
        )
    items.sort(key=lambda item: item["label"])
    return items


def _normalize_code(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(3)

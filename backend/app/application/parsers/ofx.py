import re
from dataclasses import dataclass
from datetime import datetime

from app.application.bank_catalog import load_bank_catalog, normalize_bank_code
from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction
from app.application.normalization.amount import parse_amount
from app.application.parsers.csv import _normalize_header

STMT_BLOCK_PATTERN = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class OfxAccountMetadata:
    bank_name: str | None = None
    bank_code: str | None = None
    bank_branch: str | None = None
    account_number: str | None = None


def parse_ofx_account_metadata(raw_bytes: bytes) -> OfxAccountMetadata:
    text = _decode_ofx_bytes(raw_bytes)
    accounts = re.findall(r"<BANKACCTFROM>\s*(.*?)</BANKACCTFROM>", text, re.IGNORECASE | re.DOTALL)
    # The transaction parser combines accounts; do not label that combined
    # result as belonging to only one of several source accounts.
    if len(accounts) != 1:
        return OfxAccountMetadata()
    account = accounts[0]
    raw_code = _extract_optional_tag(account, "BANKID") or ""
    code = normalize_bank_code(raw_code) if re.fullmatch(r"\d{1,9}", raw_code) else None
    bank = next((item for item in load_bank_catalog() if item.code == code), None)
    return OfxAccountMetadata(
        bank_name=(bank.short_name or bank.name) if bank else None,
        bank_code=code,
        bank_branch=_extract_optional_tag(account, "BRANCHID"),
        account_number=_extract_optional_tag(account, "ACCTID"),
    )


def parse_ofx_transactions(raw_bytes: bytes) -> list[NormalizedTransaction]:
    text = _decode_ofx_bytes(raw_bytes)
    blocks = STMT_BLOCK_PATTERN.findall(text)
    if not blocks:
        raise InvalidFileContentError("OFX does not contain STMTTRN entries.")

    transactions: list[NormalizedTransaction] = []
    for block in blocks:
        date_raw = _extract_required_tag(block, "DTPOSTED")
        amount_raw = _extract_required_tag(block, "TRNAMT")
        description_raw = _extract_optional_tag(block, "MEMO") or _extract_optional_tag(block, "NAME")
        if not description_raw:
            raise InvalidFileContentError("OFX transaction is missing MEMO/NAME.")
        type_raw = _extract_optional_tag(block, "TRNTYPE") or ""

        amount = parse_amount(amount_raw)
        transactions.append(
            NormalizedTransaction(
                date=_parse_ofx_date(date_raw),
                description=description_raw.strip(),
                amount=amount,
                type=_normalize_type(type_raw, amount),
            )
        )

    if not transactions:
        raise InvalidFileContentError("OFX does not contain transaction rows.")
    return transactions


def _decode_ofx_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InvalidFileContentError("Unable to decode OFX bytes.")


def _extract_required_tag(block: str, tag: str) -> str:
    value = _extract_optional_tag(block, tag)
    if not value:
        raise InvalidFileContentError(f"OFX transaction is missing '{tag}'.")
    return value


def _extract_optional_tag(block: str, tag: str) -> str | None:
    # OFX SGML usually has unclosed tags like <DTPOSTED>20260401...
    match = re.search(rf"<{tag}>\s*([^\r\n<]+)", block, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _parse_ofx_date(raw: str) -> str:
    cleaned = re.sub(r"[^0-9]", "", raw)
    if len(cleaned) < 8:
        raise InvalidFileContentError(f"Invalid OFX date value: {raw!r}.")
    try:
        return datetime.strptime(cleaned[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid OFX date value: {raw!r}.") from exc


def _normalize_type(raw_type: str, amount: float) -> str:
    value = _normalize_header(raw_type)
    if value in {"credit", "credito", "inflow"}:
        return "inflow"
    if value in {"debit", "debito", "outflow"}:
        return "outflow"
    return "inflow" if amount >= 0 else "outflow"


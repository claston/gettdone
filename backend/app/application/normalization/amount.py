import re

from app.application.errors import InvalidFileContentError


def parse_amount(raw: object) -> float:
    original = raw
    value = str(raw).strip().replace("\u2212", "-")
    value = re.sub(r"[^\d,.\-+()Rr$\s]", "", value)
    negative = value.startswith("(") and value.endswith(")")
    value = value.replace("(", "").replace(")", "")
    value = value.replace("R$", "").replace("r$", "").replace(" ", "")

    if value.endswith("-"):
        negative = True
        value = value[:-1]
    elif value.endswith("+"):
        value = value[:-1]

    if value.startswith("-"):
        negative = True
        value = value[1:]
    elif value.startswith("+"):
        value = value[1:]

    value = _normalize_numeric_separators(value)

    try:
        parsed = float(value)
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid amount value: {original!r}.") from exc

    if negative and parsed > 0:
        return -parsed
    return parsed


def apply_amount_role_sign(amount: float, role: str | None) -> float:
    if role == "credit":
        return abs(amount)
    if role == "debit":
        return -abs(amount)
    return amount


def _normalize_numeric_separators(value: str) -> str:
    separator_count = value.count(",") + value.count(".")
    if separator_count == 0:
        return value

    if separator_count > 1:
        chunks = re.split(r"[,.]", value)
        if len(chunks) > 1 and all(chunk.isdigit() and len(chunk) == 3 for chunk in chunks[1:]):
            return "".join(chunks)

    last_comma = value.rfind(",")
    last_dot = value.rfind(".")
    last_separator_index = max(last_comma, last_dot)
    if last_separator_index < 0:
        return value

    integer_part = re.sub(r"[,.]", "", value[:last_separator_index])
    fractional_part = re.sub(r"[,.]", "", value[last_separator_index + 1 :])
    if not fractional_part:
        return integer_part
    return f"{integer_part}.{fractional_part}"

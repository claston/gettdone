from app.application.errors import InvalidFileContentError


def parse_amount(raw: object) -> float:
    original = raw
    value = str(raw).strip().replace("\u2212", "-")
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

    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")

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

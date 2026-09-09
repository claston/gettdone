"""Compatibility exports loaded on demand, without initializing sibling services."""

from importlib import import_module

_EXPORTS: dict[str, tuple[str, str]] = {
    "ParsedDocument": ("app.application.parsers.service", "ParsedDocument"),
    "ParsedOperationalSheet": ("app.application.parsers.sheet", "ParsedOperationalSheet"),
    "ParsingService": ("app.application.parsers.service", "ParsingService"),
    "parse_bank_statement_rows": ("app.application.parsers.bank_statement", "parse_bank_statement_rows"),
    "parse_csv_transactions": ("app.application.parsers.csv", "parse_csv_transactions"),
    "parse_csv_transactions_with_mapping": ("app.application.parsers.csv", "parse_csv_transactions_with_mapping"),
    "parse_ofx_transactions": ("app.application.parsers.ofx", "parse_ofx_transactions"),
    "parse_operational_sheet_rows": ("app.application.parsers.sheet", "parse_operational_sheet_rows"),
    "parse_xlsx_transactions": ("app.application.parsers.xlsx", "parse_xlsx_transactions"),
    "parse_xlsx_transactions_with_mapping": ("app.application.parsers.xlsx", "parse_xlsx_transactions_with_mapping"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    export = _EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = export
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})

from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.normalization.pdf_tabular_rules import has_declarative_table_header
from app.application.normalization.text import normalize_upper_text


def resolve_tabular_profile(
    lines: list[str], *, layout_profile: DeclarativeLayoutProfile | None
) -> DeclarativeLayoutProfile | None:
    if has_declarative_table_header(lines, layout_profile):
        return layout_profile
    return None


def should_ignore_profile_transaction_description(
    description: str,
    layout_profile: DeclarativeLayoutProfile | None,
) -> bool:
    if layout_profile is None or layout_profile.schema_version < 2:
        return False
    return _matches_declared_row(description, layout_profile.parsing.ignore_rows)


def is_profile_opening_balance_description(
    description: str,
    layout_profile: DeclarativeLayoutProfile | None,
) -> bool:
    if layout_profile is None or layout_profile.schema_version < 2:
        return False
    return _matches_declared_row(description, layout_profile.parsing.opening_balance_rows)


def should_import_profile_opening_balance(layout_profile: DeclarativeLayoutProfile | None) -> bool:
    if layout_profile is None or layout_profile.schema_version < 2:
        return True
    return layout_profile.parsing.opening_balance_policy == "import"


def _matches_declared_row(description: str, declared_rows: tuple[str, ...]) -> bool:
    normalized_description = normalize_upper_text(description)
    if not normalized_description:
        return False
    for declared_row in declared_rows:
        normalized_declared_row = normalize_upper_text(declared_row)
        if not normalized_declared_row:
            continue
        if normalized_description == normalized_declared_row:
            return True
        if normalized_description.startswith(f"{normalized_declared_row} "):
            return True
    return False

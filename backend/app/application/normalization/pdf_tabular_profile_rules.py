from app.application.layout_profiles.registry import DeclarativeLayoutProfile
from app.application.normalization.pdf_tabular_rules import has_declarative_table_header


def resolve_tabular_profile(
    lines: list[str], *, layout_profile: DeclarativeLayoutProfile | None
) -> DeclarativeLayoutProfile | None:
    if has_declarative_table_header(lines, layout_profile):
        return layout_profile
    return None

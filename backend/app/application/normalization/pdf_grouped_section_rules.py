from app.application.normalization.pdf_text_rules import section_hint


def resolve_grouped_section_hint(raw_text: str, *, current_hint: str | None) -> str | None:
    maybe_hint = section_hint(raw_text)
    if maybe_hint:
        return maybe_hint
    return current_hint

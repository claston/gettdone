import re

from app.application.normalization.pdf_text_rules import should_ignore_line

_PAGE_COUNTER_PATTERN = re.compile(r"^\d+\s+DE\s+\d+$")
_GROUPED_ICON_LINES = {"↑", "↓", "?"}
_GROUPED_CONTEXT_LINES = {"SALDO DO DIA", "SALDO DIA"}


def should_ignore_grouped_line(normalized_line: str) -> bool:
    return (
        should_ignore_line(normalized_line)
        or _PAGE_COUNTER_PATTERN.fullmatch(normalized_line) is not None
        or normalized_line in _GROUPED_ICON_LINES
        or normalized_line in _GROUPED_CONTEXT_LINES
    )

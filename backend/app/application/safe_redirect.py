"""Restrict authentication redirects to local absolute paths."""
import re


def safe_next_path(value: str | None, fallback: str = "/client-area.html") -> str:
    raw = str(value or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or re.search(r"[\\\x00-\x20\x7f]", raw):
        return fallback
    return raw

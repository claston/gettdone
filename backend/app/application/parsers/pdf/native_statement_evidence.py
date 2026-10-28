"""Alternative native views must retain every printed financial token."""

import re
from collections import Counter


def same_printed_financial_fields(first, second):
    number = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
    patterns = (rf"(?<![\d.,]){number}(?!\d)", r"\b\d{2}/\d{2}/\d{4}\b", rf"({number})\s*\(([+-])\)")
    left, right = "\n".join(first), "\n".join(second)
    return all((observed := Counter(re.findall(pattern, left)))
               and observed == Counter(re.findall(pattern, right)) for pattern in patterns)

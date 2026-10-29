"""Associate a printed amount before a page break with its following dated row.

Only an otherwise empty first amount cell, an extra terminal amount and an
independent printed C/D sign establish this association. Physical source boxes
are never moved; readers decide how to attach their evidence.
"""

import re
from dataclasses import dataclass

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import NUMBER


@dataclass(frozen=True)
class PageAmountFragment:
    origin_page: object
    amount: object
    target_page: object
    anchor: object


def preceding_page_amounts(anchors, amounts, witnesses):
    """Inputs are (page number + top, original page, original line) triples."""
    groups = {}
    for item in anchors:
        groups.setdefault(item[1].page_number, []).append(item)
    result = []
    for number, previous in groups.items():
        if number + 1 not in groups:
            continue
        previous = sorted(previous, key=lambda item: item[0])
        following = sorted(groups[number + 1], key=lambda item: item[0])
        before, after = previous[-1], following[0]
        if before[2].bbox["top"] < .8 or after[2].bbox["top"] > .15:
            continue
        stop = following[1][0] - .005 if len(following) > 1 else number + 2
        target_cells = [line for pos, page, line in witnesses if page.page_number == number + 1
                        and after[0] - .005 <= pos < stop]
        if any(re.match(r"^(?:\d+\s+)?SALDO\b", upper(line.text)) for line in target_cells):
            continue
        if any(after[0] - .005 <= pos < stop for pos, _, _ in amounts):
            continue
        tail = sorted([item for item in amounts if item[1].page_number == number
                       and item[0] >= before[0] - .005], key=lambda item: item[0])
        if len(tail) != 2:
            continue
        _, origin, candidate = tail[-1]
        if (candidate.bbox["top"] < .88
                or candidate.bbox["top"] - tail[0][2].bbox["top"] < max(candidate.bbox["height"], .006)
                or not re.fullmatch(NUMBER, candidate.text.strip())):
            continue
        signs = [line for line in target_cells if line.text in {"C", "D"}
                 and -.02 <= line.bbox["left"] - (candidate.bbox["left"] + candidate.bbox["width"]) <= .04]
        if len(signs) != 1:
            continue
        result.append(PageAmountFragment(origin, candidate, after[1], after[2]))
    return result

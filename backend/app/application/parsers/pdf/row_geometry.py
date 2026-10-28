"""Associate real cells with their nearest printed row, never with a balance gap."""

from statistics import median

from app.application.parsers.pdf.verified_statements import fail


def row_slope(headers):
    """Measure scan skew from labels known to share a printed header baseline."""
    slopes = []
    for i, a in enumerate(headers):
        for b in headers[i + 1 :]:
            dx = b.bbox["left"] - a.bbox["left"]
            if abs(dx) > 0.05:
                dy = (b.bbox["top"] + b.bbox["height"] / 2) - (a.bbox["top"] + a.bbox["height"] / 2)
                if abs(dy) < 0.03:
                    slopes.append(dy / dx)
    slope = median(slopes) if slopes else 0
    return slope if abs(slope) < 0.08 else 0


def local_row_slopes(anchors, descriptions, *, fallback=0):
    """Use independent date/history labels to follow perspective along a scan."""
    matched = {}
    for description in descriptions:
        anchor = nearest_row(description, anchors)
        if anchor is not None and anchor is not description:
            matched.setdefault(anchor.id, []).append(description)
    return {
        anchor.id: row_slope([anchor, matched[anchor.id][0]]) if len(matched.get(anchor.id, [])) == 1 else fallback for anchor in anchors
    }


def nearest_row(cell, anchors, *, slope=0, slopes=None, max_distance=0.03):
    center = cell.bbox["top"] + cell.bbox["height"] / 2
    candidates = sorted(
        (
            abs(
                center
                - (slopes or {}).get(anchor.id, slope) * (cell.bbox["left"] - anchor.bbox["left"])
                - (anchor.bbox["top"] + anchor.bbox["height"] / 2)
            ),
            index,
        )
        for index, anchor in enumerate(anchors)
    )
    if not candidates or candidates[0][0] > max_distance:
        return None
    if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 0.0001:
        fail("Uma célula financeira está entre duas linhas e precisa de revisão.")
    return anchors[candidates[0][1]]

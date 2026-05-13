from app.application.normalization.pdf_amount_tokens import is_amount_like


def next_columnar_block_index(lines: list[str], *, current_index: int) -> int:
    next_index = current_index + 4
    if next_index < len(lines) and is_amount_like(lines[next_index].strip()):
        next_index += 1
    return next_index

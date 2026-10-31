"""First opt-in for shared account scopes: Santander Consolidado Inteligente."""

import os
import re
from dataclasses import replace
from hashlib import sha256
from io import BytesIO

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.layout_specific.santander_ocr import _period
from app.application.parsers.pdf.statement_account_blocks import AccountHeader, account_block_plan, account_scope, combine_account_results
from app.application.parsers.pdf.statement_blocks import slice_extraction
from app.application.parsers.pdf.statement_review import apply_statement_review, statement_review_scope
from app.application.parsers.pdf.verified_statements import fail

_ACCOUNT = r"\d[\d.]*-\d+"


def _header(text):
    normalized = upper(text)
    if not re.search(r"\bRESUMO\s*[-–]\s*[A-Z]+/20\d{2}", normalized):
        return None
    # Only the owner's labelled summary header, before balances and movements.
    header = re.split(r"\(?=\)?\s*SALDO DE CONTA CORRENTE|\bTOTAL DE CREDITOS\b", normalized, maxsplit=1)[0]
    columns = re.findall(rf"\bAGENCIA\s+CONTA CORRENTE\s+(\d{{4}})\s+({_ACCOUNT})\b", header)
    flowing = re.findall(rf"\bAGENCIA\s+(\d{{4}})\s+CONTA CORRENTE\s+({_ACCOUNT})\b", header)
    identities = set(columns + flowing)
    owner = re.search(r"\bNOME\s*\n\s*([^\n]+)", header)
    if owner is None:
        owner = re.search(r"\bNOME\s+(.+?)\s+AGENCIA\b", " ".join(header.split()))
    if len(identities) != 1 or owner is None:
        fail("O resumo de uma conta Santander está sem agência, conta ou titular inequívocos.")
    branch, account = identities.pop()
    start, end = _period(normalized)
    return AccountHeader(account_scope(bank_code="033", bank_branch=branch, account_number=account,
                                       holder_name=owner[1].strip()), (start.isoformat(), end.isoformat()))


def _plan(pages):
    if os.getenv("PDF_MULTI_ACCOUNT_ENABLED", "false").strip().lower() != "true":
        return []
    text = upper("\n".join(pages))
    if "SANTANDER" not in text or "EXTRATO CONSOLIDADO INTELIGENTE" not in text:
        return []
    if sum(bool(re.search(r"\bRESUMO\s*[-–]\s*[A-Z]+/20\d{2}", upper(page))) for page in pages) < 2:
        return []
    plan = account_block_plan(pages, _header)
    if len({account["holder_name"] for _, account in plan}) > 1:
        fail("Os titulares dos blocos Santander não podem ser confirmados como um único extrato consolidado.")
    return plan


def _review_block(result):
    # Pagination belongs to the complete uploaded document, not its account slice.
    reviewed = apply_statement_review(replace(result, source_page_texts=()))
    return replace(reviewed, source_page_texts=result.source_page_texts)


def parse_native_account_blocks(raw, pages, build_result):
    plan = _plan(pages)
    if not plan:
        return None
    from pypdf import PdfReader, PdfWriter

    from app.application.parsers.pdf.native_cash_columns import parse_native_cash_columns
    from app.application.parsers.pdf.text_extraction import read_layout_native_pdf_page_texts

    reader = PdfReader(BytesIO(raw))
    results = []
    for block, _ in plan:
        writer, buffer = PdfWriter(), BytesIO()
        for page in reader.pages[block.start:block.stop]:
            writer.add_page(page)
        writer.write(buffer)
        content = buffer.getvalue()
        with statement_review_scope():
            parsed = parse_native_cash_columns(content, read_layout_native_pdf_page_texts(content))
            if parsed is None:
                fail("Um bloco Santander não pôde ser convertido integralmente.")
            statement, texts = parsed
            results.append(_review_block(build_result(statement, texts)))
    return combine_account_results(results, plan, pages, document_hash=sha256(raw).hexdigest())


def parse_ocr_account_blocks(extraction, parse, build_result):
    from app.application.parsers.pdf.layout_specific.santander_ocr import _cells, _summary
    from app.application.parsers.pdf.native_cash_columns import parse_santander_inactive_cash

    pages = ["\n".join(line.text for line in page.lines) for page in extraction.pages]
    plan = _plan(pages)
    if not plan:
        return None
    results = []
    for block, _ in plan:
        with statement_review_scope():
            sliced = slice_extraction(extraction, block)
            controls = _summary(_cells(sliced))[2]
            ordered_texts = ["\n".join(line.text for line in sorted(page.lines,
                key=lambda line: (round(line.bbox["top"] / .004), line.bbox["left"])))
                for page in sliced.pages] if all(line.bbox for page in sliced.pages for line in page.lines) else []
            inactive = (parse_santander_inactive_cash(ordered_texts)
                        if controls["credit"] == 0 and controls["debit"] == 0 else None)
            if inactive is not None:
                result = build_result(inactive, ordered_texts)
            else:
                result = parse(sliced)
                if result.parse_metrics.get("selected_parser") != "layout_specific_santander_ocr_columns":
                    fail("As colunas de um bloco Santander não puderam ser lidas com segurança.")
            result = replace(result, parse_metrics={**result.parse_metrics,
                "printed_opening_balance": float(controls["opening"]), "printed_closing_balance": float(controls["closing"])})
            results.append(_review_block(result))
    return combine_account_results(results, plan, pages, original_extraction=extraction,
                                   document_hash=extraction.document_hash)

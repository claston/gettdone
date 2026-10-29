"""Conservative shared holder recognition from native text and OCR, with source references."""

import re
import unicodedata

from app.application.organization_suggestions import build_source_evidence

TAX = r"(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2})"


def normalized(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _plain(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).upper()


def identify_statement(analysis, *, page_texts=(), representation="native"):
    source = build_source_evidence(analysis)
    holders, taxes, references, accounts = set(), set(), [], set()
    if source["holder_name"]:
        holders.add(source["holder_name"])
    if source["holder_tax_id"]:
        taxes.add(source["holder_tax_id"])
    for page, text in enumerate(page_texts or (), 1):
        lines = str(text).splitlines()
        for index, line in enumerate(lines):
            # Only explicit holder labels; arbitrary tax numbers and transaction names are excluded.
            plain = _plain(line.strip())
            name = re.match(r"^(?:TITULAR|NOME DO TITULAR|RAZAO SOCIAL|EMPRESA/CNPJ)\s*[:\-]\s*(.+)", plain)
            if name:
                raw = re.split(r"[:\-]\s*", line.strip(), maxsplit=1)[1]
                raw = re.split(r"\s*(?:\||CPF|CNPJ)\s*[:\-]?", raw, maxsplit=1, flags=re.I)[0].strip()
                if raw and len(raw) <= 160:
                    holders.add(raw)
                    references.append({"page": page, "line": index + 1})
            tax = re.match(r"^(?:CPF\s*/\s*CNPJ|CNPJ\s*/\s*CPF|CPF|CNPJ)\s+(?:DO\s+)?TITULAR\s*[:\-]?\s*(" + TAX + r")\b", plain)
            if not tax and name:
                tax = re.search(r"\b(" + TAX + r")\b", plain)
                if not tax and index + 1 < len(lines):
                    tax = re.match(r"^\s*(?:CPF\s*/\s*CNPJ|CNPJ|CPF)\s*:\s*(" + TAX + r")\b", _plain(lines[index + 1]))
            if tax:
                taxes.add(normalized(tax[1]))
                references.append({"page": page, "line": index + 1})
            account = re.match(r"^AGENCIA\s*:\s*([0-9.\-]+)\s+(?:CONTA(?: CORRENTE)?)\s*:\s*([0-9.\-]+)", plain)
            if account:
                accounts.add((account[1], account[2]))
                references.append({"page": page, "line": index + 1})
    source.update(
        holder_name=next(iter(holders)) if len(holders) == 1 else source["holder_name"],
        holder_tax_id=next(iter(taxes)) if len(taxes) == 1 else "",
        confidence="ambiguous" if len(taxes) > 1 else "strong" if len(taxes) == 1 else "name" if holders else "missing",
        references=references[:40], representation=representation,
    )
    if len(accounts) == 1:
        source["branch"], source["account_number"] = next(iter(accounts))
    if len(accounts) > 1:
        # Do not pick an arbitrary source account from a multi-account document.
        source["branch"], source["account_number"] = "", ""
    source["accounts"] = [{"bank_code": source["bank_code"], "branch": branch, "account_number": number}
                          for branch, number in sorted(accounts)]
    return source

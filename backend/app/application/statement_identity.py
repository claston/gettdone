"""Conservative shared holder recognition from native text and OCR, with source references."""

import hashlib
import re
import unicodedata

from app.application.organization_suggestions import build_source_evidence

TAX = r"(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2})"
IDENTITY_VERSION = 2
IDENTIFIER = r"\d[\d.]*(?:-[\dXx]+)?(?=$|[\s/|])"
HEADER_FIELDS = re.compile(r"(?=(?:COOPERATIVA|AG[EÊ]NCIA|CONTA(?: CORRENTE)?|PER[IÍ]ODO)\s*:)", re.I)


def normalized(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _plain(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).upper()


def account_identity(source):
    """Typed origin scope: never equate a cooperative number with a bank agency."""
    bank, branch, cooperative, number = (normalized(source.get(key))
        for key in ("bank_code", "branch", "cooperative", "account_number"))
    return (bank, branch, cooperative, number) if bank and bank != "000" and number and (branch or cooperative) else None


def account_signature(source):
    identity = account_identity(source)
    return hashlib.sha256("\x1f".join(identity).encode()).hexdigest() if identity else None


def _header_blocks(text):
    block, active = [], True
    for index, raw in enumerate(str(text).splitlines(), 1):
        plain = _plain(raw.strip())
        if re.match(r"^EXTRATO(?:\s+DE\s+CONTA|\s+BANCARIO)\b", plain):
            if block:
                yield block
            block, active = [], True
        date = r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
        header_date = re.fullmatch(date + r"\s*(?:-\s*)?(?:\d{1,2}:\d{2}(?::\d{2})?|" + date + r")", plain)
        dated_movement = re.match(r"^\d{1,2}[/.-]\d{1,2}\b", plain) and not header_date
        if (dated_movement
                or plain.startswith(("HISTORICO DE MOVIMENTACAO", "MOVIMENTACOES", "LANCAMENTOS"))
                or plain == "DATA"
                or plain.startswith("DATA") and any(word in plain for word in ("VALOR", "HISTORICO", "DESCRICAO"))):
            if block:
                yield block
            block, active = [], False
        if active:
            # Native PDFs can concatenate adjacent columns without spaces; keep their physical line reference.
            block.extend((index, part.strip()) for part in HEADER_FIELDS.split(raw) if part.strip())
    if block:
        yield block


def _joined_header_fields(block):
    """Join a standalone label to its next value, keeping the physical label line."""
    result, index = [], 0
    labels = r"(?:COOPERATIVA|AGENCIA|CONTA(?: CORRENTE)?|C/C|TITULAR|NOME DO TITULAR|RAZAO SOCIAL|ASSOCIADO|EMPRESA/CNPJ)"
    while index < len(block):
        line_number, line = block[index]
        if (re.fullmatch(labels + r"\s*:\s*", _plain(line)) and index + 1 < len(block)
                and not re.match(r"^[^:]+:", block[index + 1][1])):
            line += " " + block[index + 1][1]
            index += 1
        result.append((line_number, line))
        index += 1
    return result


def identify_statement(analysis, *, page_texts=(), representation="native"):
    source = build_source_evidence(analysis)
    holders, taxes, references, accounts = {}, set(), [], []
    if source["holder_name"]:
        holders[" ".join(_plain(source["holder_name"]).split())] = source["holder_name"]
    if source["holder_tax_id"]:
        taxes.add(source["holder_tax_id"])
    for page, text in enumerate(page_texts or (), 1):
        for block in _header_blocks(text):
            block = _joined_header_fields(block)
            branches, cooperatives, numbers = set(), set(), set()
            for position, (line_number, line) in enumerate(block):
                plain = _plain(line)
                name = re.match(r"^(?:TITULAR|NOME DO TITULAR|RAZAO SOCIAL|EMPRESA/CNPJ|ASSOCIADO)\s*[:\-]\s*(.+)", plain)
                raw_name = ""
                if name:
                    raw_name = re.split(r"[:\-]\s*", line, maxsplit=1)[1]
                    raw_name = re.split(r"\s*(?:\||CPF|CNPJ)\s*[:\-]?", raw_name, maxsplit=1, flags=re.I)[0].strip()
                tax = re.match(r"^(?:CPF\s*/\s*CNPJ|CNPJ\s*/\s*CPF|CPF|CNPJ)\s+(?:DO\s+)?TITULAR\s*[:\-]?\s*(" + TAX + r")\b", plain)
                if not tax and name:
                    tax = re.search(r"\b(" + TAX + r")\b", plain)
                    if not tax and position + 1 < len(block):
                        tax = re.match(r"^\s*(?:CPF\s*/\s*CNPJ|CNPJ|CPF)\s*:\s*(" + TAX + r")\b", _plain(block[position + 1][1]))
                if tax:
                    taxes.add(normalized(tax[1]))
                    references.append((page, line_number))
                for label, values in (("AGENCIA", branches), ("COOPERATIVA", cooperatives)):
                    identifier = re.match(r"^" + label + r"\s*:\s*(" + IDENTIFIER + ")", plain)
                    if identifier:
                        values.add(identifier[1])
                        references.append((page, line_number))
                account = re.match(r"^(?:CONTA(?: CORRENTE)?|C/C)\s*:\s*(" + IDENTIFIER + ")", line, re.I)
                if account:
                    numbers.add(account[1])
                    references.append((page, line_number))
                    suffix = line[account.end():].strip()
                    if suffix.startswith("/"):
                        candidate = suffix[1:].strip()
                        if re.search(r"[A-Z]", _plain(candidate)) and _plain(candidate) not in {
                            "POUPANCA", "CONTA CORRENTE", "CONTA SALARIO", "CORRENTE",
                        }:
                            raw_name = candidate
                if raw_name and len(raw_name) <= 160:
                    holders.setdefault(" ".join(_plain(raw_name).split()), raw_name)
                    references.append((page, line_number))
            for number in sorted(numbers):
                branch = next(iter(branches)) if len(branches) == 1 else ""
                cooperative = next(iter(cooperatives)) if len(cooperatives) == 1 else ""
                if not branch and not cooperative and normalized(number) == normalized(source["account_number"]):
                    branch = source["branch"]
                accounts.append({"bank_code": source["bank_code"], "branch": branch,
                                 "cooperative": cooperative, "account_number": number})
    # Repeated page headers and punctuation variations do not create multiple source accounts.
    accounts = list({(a["bank_code"], normalized(a["branch"]), normalized(a["cooperative"]), normalized(a["account_number"])): a
                     for a in accounts}.values())
    source.update(
        version=IDENTITY_VERSION,
        holder_name=next(iter(holders.values())) if len(holders) == 1 else "",
        holder_tax_id=next(iter(taxes)) if len(taxes) == 1 else "",
        cooperative="", references=[{"page": p, "line": n} for p, n in dict.fromkeys(references)][:40],
        representation=representation, accounts=accounts,
    )
    if len(accounts) == 1:
        source.update(accounts[0])
    if len(accounts) > 1:
        source["branch"], source["account_number"] = "", ""
    source["confidence"] = ("ambiguous" if len(taxes) > 1 or len(holders) > 1 else "strong" if len(taxes) == 1
                            else "source_account" if holders and account_identity(source) else "name" if holders else "missing")
    return source

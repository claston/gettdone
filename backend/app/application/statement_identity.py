"""Conservative shared holder recognition from native text and OCR, with source references."""

import hashlib
import re
import unicodedata

from app.application.organization_suggestions import build_source_evidence

TAX = r"(?:\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2})"
IDENTITY_VERSION = 3
IDENTIFIER = r"\d[\d.]*(?:-[\dXx]+)?(?=$|[\s/|A-Za-zÀ-ÿ])"
ORIGIN_LABEL = r"(?:COOPERATIVA|COOP\.|AG[EÊ]NCIA|AG\.?|CONTA(?:\s+(?:CORRENTE|INVESTIMENTO))?|C/C|CC|BANCO)"
TAX_LABEL = r"(?:CPF\s*/\s*CNPJ|CNPJ\s*/\s*CPF|CNPJ|CPF)"
NAME_LABEL = r"(?:TITULAR|NOME DO TITULAR|RAZAO SOCIAL|ASSOCIADO|EMPRESA/CNPJ|CLIENTE|NOME)"
HEADER_FIELDS = re.compile(r"(?=(?<!/)(?:" + ORIGIN_LABEL + r"|PER[IÍ]ODO|" + TAX_LABEL + r")\s*(?::|(?=\d)))", re.I)


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
    lines = str(text).splitlines()
    for index, raw in enumerate(lines, 1):
        # These native font characters encode punctuation, never missing digits.
        raw = raw.replace("\ue088", "-")
        raw = re.sub(r"(?<=\d)\s+-\s+(?=[\dXx](?:\s|$))", "-", raw)
        raw = re.sub(r"(?<=[A-Za-zÀ-ÿ])\.{2,}(?=\s*:)", "", raw)
        raw = re.sub(r"(" + TAX_LABEL + r"\s*:\s*" + TAX + r")\s+(\d+)\s+(\d[\d.-]*)"
                     r"\s*Ag[eê]ncia:\s*Conta:", r"\1 Agência: \2 Conta: \3", raw, flags=re.I)
        plain = _plain(raw.strip())
        if re.match(r"^EXTRATO(?:\s+(?:CONSOLIDADO\s+)?(?:DE\s+)?CONTA|\s+BANCARIO)\b", plain):
            if block:
                yield block
            block, active = [], True
        date = r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
        header_date = (re.fullmatch(date + r"\s*(?:[-,]|A|ATE)?\s*(?:\d{1,2}:\d{2}(?::\d{2})?"
                                   r"(?:\s+INTERNET BANKING(?:\s+\w+)*)?|" + date + r")", plain)
                       or re.fullmatch(date + r"\s+EXTRATO\s+(?:DE\s+)?CONTA CORRENTE\s+\d{2}:\d{2}:\d{2}", plain)
                       or re.fullmatch(date, plain) and index < len(lines)
                       and re.match(r"^(?:" + ORIGIN_LABEL + r"|" + TAX_LABEL + r")\s*:", _plain(lines[index].strip())))
        if re.fullmatch(date, plain) and any(re.match(r"^(?:DATA (?:REF|PROC)|HORA PROC)\s*:", _plain(line))
                                                for _, line in block[-8:]):
            header_date = True
        dated_movement = re.match(r"^\d{1,2}[/.-]\d{1,2}\b", plain) and not header_date
        if (dated_movement
                or plain.startswith(("HISTORICO DE MOVIMENTACAO", "MOVIMENTACOES", "LANCAMENTOS"))
                or plain.startswith(("FALE COM NOSSA CENTRAL", "OUVIDORIA:", "SAC:", "©", "COPYRIGHT"))
                or plain == "DATA"
                or plain.startswith("NOME ") and "DATA DE PAGAMENTO" in plain
                or plain.startswith(("DIA ", "DT. ", "IDENTIFICACAO")) and any(
                    word in plain for word in ("HISTORICO", "LANCAMENTO"))
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
    labels = r"(?:" + ORIGIN_LABEL + r"|" + NAME_LABEL + r"|DOCUMENTO|" + TAX_LABEL + r")"
    while index < len(block):
        line_number, line = block[index]
        if (re.fullmatch(labels + r"\s*:?\s*", _plain(line)) and index + 1 < len(block)
                and not re.match(r"^[^:]+:", block[index + 1][1])
                and not re.fullmatch(labels, _plain(block[index + 1][1]))):
            line = line.rstrip(":") + ": " + block[index + 1][1]
            index += 1
        result.append((line_number, line))
        index += 1
    return result


def _holder_candidate(value):
    value = value.strip(" /|•·\t")
    value = re.sub(r"^Per[ií]odo\s+de\s+\d{1,2}/\d{1,2}/\d{4}\s+a\s+\d{1,2}/\d{1,2}/\d{4}\s+[–-]\s+", "", value, flags=re.I)
    plain = _plain(value)
    if (not 3 <= len(value) <= 160 or not re.search(r"[A-Z]", plain) or ":" in value
            or all(len(word) == 1 for word in plain.split())
            or "©" in value or re.search(r"\bCOPYRIGHT\b", plain)
            or re.match(r"^(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+\d{4}\b", plain)
            or re.search(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|R\$", plain)
            or re.search(r"\b(?:SCFI|INSTITUICAO DE PAGAMENTO)\b", plain)
            or re.match(r"^(?:EXTRATO|SALDO|DADOS|INFORMACOES|PERIODO|PAGINA|EMITIDO|GERADO|DATA|"
                        r"BANCO\b|INSTITUICAO\b|COOPERATIVA\b|AGENCIA\b|CONTA\b|CPF\b|CNPJ\b|"
                        r"NOME\b|TITULAR\b|CLIENTE\s*-|DOCUMENTO\b|INVESTIMENTO\b|MENSAL\b|PDF\b|"
                        r"MINHA\b|MEU\b|SUA\b|SEU\b)", plain)):
        return ""
    return value


def _adjacent_holders(block):
    """Accept unlabeled names only beside an explicit origin account or owner tax field."""
    origin = any(re.match(r"^(?:CONTA(?:\s+(?:CORRENTE|INVESTIMENTO))?|C/C|CC)\s*:?\s*" + IDENTIFIER,
                          _plain(line)) for _, line in block)
    account_section = any(_plain(line) == "DADOS DA CONTA" for _, line in block)
    for position, (line_number, line) in enumerate(block[:-1]):
        if re.fullmatch(r"NOME\s*/\s*RAZAO SOCIAL\s+CPF\s*/\s*CNPJ", _plain(line)):
            ref, value = block[position + 1]
            pair = re.fullmatch(r"(.+?)\s+(" + TAX + r")", value)
            if pair and (name := _holder_candidate(pair[1])):
                yield name, normalized(pair[2]), (line_number, ref)
    if not origin:
        return
    for position, (line_number, line) in enumerate(block):
        plain = _plain(line)
        tax = re.match(r"^" + TAX_LABEL + r"\s*:?\s*(" + TAX + r")(?!\d)", plain)
        if account_section:
            tax = tax or re.match(r"^DOCUMENTO\s*:\s*(" + TAX + r")(?!\d)", plain)
        if tax:
            # Tax labels belonging to a bank/issuer do not establish holder identity.
            candidates = [(line_number, line[tax.end():])]
            if position and line_number - block[position - 1][0] <= 1:
                previous_number, previous = block[position - 1]
                candidates.append((previous_number, previous))
                if (position > 1 and re.match(r"^CONTA\s+INVESTIMENTO\s*:", _plain(previous))
                        and line_number - block[position - 2][0] <= 2):
                    candidates.append(block[position - 2])
            for ref, candidate in candidates:
                name = _holder_candidate(candidate)
                if name:
                    yield name, normalized(tax[1]), (ref, line_number)
            if account_section:
                yield "", normalized(tax[1]), (line_number,)
        # The holder may precede agency/account fields, even on the same physical line.
        if (re.match(r"^(?:AGENCIA|AG\.?)\s*:?\s*" + IDENTIFIER, plain) and position
                and not any(re.match(r"^" + NAME_LABEL + r"\s*[:\-]", _plain(value)) for _, value in block)):
            ref, previous = block[position - 1]
            if line_number - ref <= 1 and (name := _holder_candidate(previous)):
                yield name, "", (ref,)
        if re.match(r"^BANCO\s*:?\s*\d{3}\b", plain) and position:
            ref, previous = block[position - 1]
            if ref == line_number and (name := _holder_candidate(previous)):
                yield name, "", (ref,)


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
            for name, tax, refs in _adjacent_holders(block):
                if name:
                    holders.setdefault(" ".join(_plain(name).split()), name)
                if tax:
                    taxes.add(tax)
                references.extend((page, ref) for ref in refs)
            for position, (line_number, line) in enumerate(block):
                plain = _plain(line)
                printed_bank = re.match(r"^BANCO\s*:\s*(\d{3})\b", plain)
                if printed_bank:
                    source["bank_code"] = printed_bank[1]
                    references.append((page, line_number))
                name = re.match(r"^" + NAME_LABEL + r"\s*[:\-]\s*(.+)", plain)
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
                    if raw_name:
                        raw_name = re.sub(r"\s*[-|•·]?\s*" + TAX + r"\s*$", "", raw_name).strip()
                for label, values in ((r"(?:AGENCIA|AG\.?)", branches), (r"(?:COOPERATIVA|COOP\.)", cooperatives)):
                    identifier = re.match(r"^" + label + r"\s*:?\s*(" + IDENTIFIER + ")", plain)
                    if identifier:
                        values.add(identifier[1])
                        references.append((page, line_number))
                account = re.match(r"^(?:CONTA(?:\s+(?:CORRENTE|INVESTIMENTO))?|C/C|CC)\s*:?\s*(" + IDENTIFIER + ")", line, re.I)
                composite = re.match(r"^CONTA\s*:\s*(\d{3,5})\s*[|/]\s*\d{3,5}\s*[|/]\s*(" + IDENTIFIER + ")", line, re.I)
                if composite:
                    branches.add(composite[1])
                    numbers.add(composite[2])
                    references.append((page, line_number))
                    continue
                if account and re.match(r"/\d", line[account.end():]):
                    # A displaced label followed by a period date is not an account.
                    account = None
                table_account = re.match(r"^(\d{3,8})\s*\|\s*(" + IDENTIFIER + ")", plain)
                if table_account and any(re.match(r"^AGENCIA\s*\|\s*CONTA\b", _plain(value)) for _, value in block):
                    branches.add(table_account[1])
                    numbers.add(table_account[2])
                    references.append((page, line_number))
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
                    elif suffix and not suffix.startswith(("|", "-", "•", "·")) and (candidate := _holder_candidate(suffix)):
                        # Some native PDFs concatenate account digits and the customer name.
                        raw_name = candidate
                if raw_name and _holder_candidate(raw_name):
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


def identify_native_statement(analysis, *, raw_bytes, page_texts):
    """Recover displaced native columns locally. This path never calls OCR or AWS."""
    source = identify_statement(analysis, page_texts=page_texts)
    if source["confidence"] == "ambiguous" or all(source.get(key) for key in ("holder_name", "holder_tax_id", "account_number")):
        return source
    try:
        from io import BytesIO

        from app.application.parsers.pdf.reader import open_pdf_reader

        reader = open_pdf_reader(BytesIO(raw_bytes))
        native_pages = [page.extract_text() or "" for page in reader.pages]
        source = _prefer_native_source(source, identify_statement(analysis, page_texts=native_pages), text_mode="plain")
        if source["confidence"] == "ambiguous" or all(source.get(key) for key in ("holder_name", "holder_tax_id", "account_number")):
            return source
        layout_pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except Exception:
        # A second text representation is optional; do not fail an otherwise valid conversion.
        return source
    return _prefer_native_source(source, identify_statement(analysis, page_texts=layout_pages), text_mode="layout")


def _prefer_native_source(source, candidate, *, text_mode):
    fields = ("holder_name", "holder_tax_id", "bank_code", "branch", "cooperative", "account_number")
    if not candidate["references"]:
        # Metadata repeated without header evidence cannot contradict a printed identifier.
        return source
    if candidate["confidence"] == "ambiguous":
        source["confidence"] = "ambiguous"
        return source
    conflicts = [key for key in fields if source.get(key) and candidate.get(key)
                 and normalized(_plain(source[key])) != normalized(_plain(candidate[key]))]
    if conflicts:
        source["confidence"] = "ambiguous"
        for key in conflicts:
            source[key] = ""
        return source
    if sum(bool(candidate.get(key)) for key in fields) <= sum(bool(source.get(key)) for key in fields):
        return source
    if any(source.get(key) and not candidate.get(key) for key in fields):
        return source
    # Keep references interpretable: all selected fields come from this one representation.
    candidate["references"] = [{**ref, "text_mode": text_mode} for ref in candidate["references"]]
    return candidate

"""PRO-only fiscal evidence readers. These records never enter the bank parser.

XML is treated as supplied evidence, not as online SEFAZ authentication. PDF
fields retain page/line provenance and always require human confirmation.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text

MAX_FISCAL_BYTES = 5 * 1024 * 1024
NFE_NS = "http://www.portalfiscal.inf.br/nfe"
NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
SALE_CFOPS = {
    "5101",
    "5102",
    "5103",
    "5104",
    "5105",
    "5106",
    "5116",
    "5117",
    "5118",
    "5119",
    "5120",
    "5401",
    "5402",
    "5403",
    "5405",
    "6101",
    "6102",
    "6103",
    "6104",
    "6105",
    "6106",
    "6107",
    "6108",
    "6116",
    "6117",
    "6118",
    "6119",
    "6120",
    "6401",
    "6402",
    "6403",
    "6404",
    "7101",
    "7102",
}


def tax_identifier(value):
    raw = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9./\s-]+", raw):
        return None
    token = re.sub(r"[./\s-]", "", raw)
    return token if re.fullmatch(r"(?:\d{11}|[A-Z0-9]{12}\d{2})", token) else None


def fiscal_money(value):
    if not re.fullmatch(r"\d{1,16}(?:\.\d{1,2})?", str(value or "")):
        raise ValueError("Valor fiscal ausente ou inválido.")
    return format(Decimal(value), ".2f")


def fiscal_date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        raise ValueError("Data fiscal ausente ou inválida.") from None


def _blank(kind, source):
    return {
        "schema_version": "pro_fiscal_v1",
        "kind": kind,
        "identity_key": None,
        "number": None,
        "series": None,
        "issuer": {"tax_id": None, "name": None},
        "recipient": {"tax_id": None, "name": None},
        "issued_on": None,
        "competence_on": None,
        "gross_amount": None,
        "net_amount": None,
        "currency": "BRL",
        "operation": "unknown",
        "status": "reported",
        "authority_status": "not_verified",
        "taxes": {},
        "installments": [],
        "source": source,
        "review_required": True,
    }


def parse_fiscal_xml(raw):
    if not 0 < len(raw) <= MAX_FISCAL_BYTES:
        raise ValueError("Envie uma nota XML com até 5 MB.")
    # Removing NUL catches UTF-16/32 declarations too. No DTD or external entity
    # is needed by either supported format; reject before handing bytes to Expat.
    probe = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
        raise ValueError("XML com declarações de entidades não é permitido.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("Não foi possível ler o XML fiscal.") from None
    if sum(1 for _ in root.iter()) > 50000:
        raise ValueError("XML fiscal excede o limite de elementos.")
    if root.tag in {f"{{{NFE_NS}}}nfeProc", f"{{{NFE_NS}}}NFe"}:
        namespace, kind = NFE_NS, "nfe"
    elif root.tag == f"{{{NFSE_NS}}}NFSe":
        namespace, kind = NFSE_NS, "nfse"
    else:
        raise ValueError("Envie uma NF-e 4.00 ou NFS-e nacional. DPS e lotes não são notas individuais.")
    ns = {"n": namespace}

    def find(node, path):
        return node.find("/".join("n:" + part for part in path.split("/")), ns) if node is not None else None

    def text(node, path, required=False):
        element = find(node, path)
        value = (element.text or "").strip() if element is not None else ""
        if len(value) > 600:
            raise ValueError("O XML contém um campo fiscal maior que o limite suportado.")
        if required and not value:
            raise ValueError("A nota não contém todos os campos obrigatórios.")
        return value or None

    def party(node):
        return {"tax_id": tax_identifier(text(node, "CNPJ") or text(node, "CPF")), "name": text(node, "xNome")}

    note = _blank(kind, {"format": "xml", "fields": {}})
    special_operation = False
    if kind == "nfe":
        nodes = list(root.iter(f"{{{namespace}}}infNFe"))
        if len(nodes) != 1 or nodes[0].get("versao") != "4.00":
            raise ValueError("Envie uma única NF-e do leiaute 4.00.")
        info = nodes[0]
        key = info.get("Id", "").removeprefix("NFe")
        if not re.fullmatch(r"[A-Z0-9]{44}", key):
            raise ValueError("Chave da NF-e inválida.")
        note.update(
            identity_key="nfe:" + key,
            number=text(info, "ide/nNF", True),
            series=text(info, "ide/serie", True),
            issued_on=fiscal_date(text(info, "ide/dhEmi") or text(info, "ide/dEmi")),
            issuer=party(find(info, "emit")),
            recipient=party(find(info, "dest")),
            gross_amount=fiscal_money(text(info, "total/ICMSTot/vNF", True)),
        )
        net = text(info, "cobr/fat/vLiq")
        note["net_amount"] = fiscal_money(net) if net else None
        codes = [text(item, "prod/CFOP") for item in info.findall("n:det", ns)]
        purpose = text(info, "ide/finNFe")
        note["operation"] = (
            "return"
            if purpose == "4"
            else "sale"
            if purpose == "1" and text(info, "ide/tpNF") == "1" and codes and all(c in SALE_CFOPS for c in codes)
            else "other"
        )
        for name in ("ICMS", "IPI", "PIS", "COFINS", "II", "FCP"):
            value = text(info, "total/ICMSTot/v" + name)
            if value is not None:
                note["taxes"][name] = fiscal_money(value)
        installments = info.findall("n:cobr/n:dup", ns)
        if len(installments) > 120:
            raise ValueError("A nota excede o limite de 120 parcelas.")
        for item in installments:
            due = text(item, "dVenc")
            note["installments"].append(
                {
                    "number": text(item, "nDup"),
                    "due_on": fiscal_date(due) if due else None,
                    "amount": fiscal_money(text(item, "vDup", True)),
                }
            )
        protocol_key = text(root, "protNFe/infProt/chNFe")
        if protocol_key and protocol_key != key:
            raise ValueError("O protocolo pertence a outra nota.")
        status = text(root, "protNFe/infProt/cStat")
        if status in {"101", "151", "155"}:
            note["status"] = "cancelled"
        elif status and status not in {"100", "150"}:
            note["status"] = "unknown"
    else:
        if root.get("versao") not in {"1.00", "1.01"}:
            raise ValueError("Versão da NFS-e nacional não suportada.")
        info = find(root, "infNFSe")
        if info is None or len(list(root.iter(f"{{{namespace}}}infNFSe"))) != 1:
            raise ValueError("Envie uma única NFS-e nacional.")
        dps = find(info, "DPS/infDPS")
        key = info.get("Id", "")
        if not re.fullmatch(r"NFS[A-Z0-9]{40,60}", key):
            raise ValueError("Identificador da NFS-e inválido.")
        note.update(
            identity_key="nfse:" + key,
            number=text(info, "nNFSe", True),
            series=text(dps, "serie"),
            issuer=party(find(info, "emit")),
            recipient=party(find(dps, "toma")),
            issued_on=fiscal_date(text(info, "dhProc", True)),
            declared_on=fiscal_date(text(dps, "dhEmi", True)),
            competence_on=fiscal_date(text(dps, "dCompet")),
            gross_amount=fiscal_money(text(dps, "valores/vServPrest/vServ", True)),
            net_amount=fiscal_money(text(info, "valores/vLiq", True)),
            operation="sale",
        )
        purpose = text(dps, "finNFSe") or text(dps, "IBSCBS/finNFSe")
        special_operation = purpose not in {None, "0"} or text(dps, "IBSCBS/indDoacao") == "1"
        if special_operation:
            note["operation"] = "other"
        for name in ("ISSQN", "TotalRet", "IRRF", "CSLL", "CP", "PIS", "COFINS"):
            value = text(info, "valores/v" + name) or text(dps, "valores/trib/tribFed/vRet" + name)
            if value is not None:
                note["taxes"][name] = fiscal_money(value)
        note["replaced_document_key"] = text(dps, "subst/chSubstda")
    if not note["issuer"]["tax_id"]:
        raise ValueError("Identificação do emitente ausente ou ilegível.")
    note["review_required"] = special_operation or not bool(note["recipient"]["tax_id"]) or note["status"] == "unknown"
    return note


def parse_fiscal_pages(pages):
    if not pages or len(pages) > 10:
        raise ValueError("Envie uma nota de até 10 páginas.")
    normalized = "\n".join(normalize_upper_text(p) for p in pages)
    kind = "nfse" if re.search(r"NFS[ -]?E|NOTA FISCAL.*SERVICOS", normalized) else "nfe" if "DANFE" in normalized else None
    if not kind:
        raise ValueError("O PDF não contém uma NF-e ou NFS-e reconhecível.")
    note = _blank(kind, {"format": "pdf", "pages": len(pages), "fields": {}})
    values, party_role = {}, None

    def capture(field, value, page, line):
        if value is None:
            return
        if len(value) > 600:
            raise ValueError("O PDF contém um campo fiscal maior que o limite suportado.")
        if field in values and values[field] != value:
            raise ValueError("Há campos conflitantes ou mais de uma nota no PDF. Confira o documento.")
        values[field] = value
        note["source"]["fields"][field] = {"page": page, "line": line}

    for page_no, page in enumerate(pages, 1):
        lines = page.splitlines()
        for line_no, raw in enumerate(lines, 1):
            line = normalize_upper_text(raw)
            if re.search(r"\b(?:PRESTADOR|EMITENTE)\b", line):
                party_role = "issuer"
            if re.search(r"\b(?:TOMADOR|DESTINATARIO)\b", line):
                party_role = "recipient"
            tax = re.search(r"(?:CNPJ|CPF)\s*:?\s*([A-Z0-9][A-Z0-9./ -]{9,22})", line)
            if tax and party_role:
                capture(party_role + "_tax", tax_identifier(tax[1].strip()), page_no, line_no)
            for field, pattern in (
                ("gross_amount", r"VALOR (?:TOTAL DA NOTA|DOS SERVICOS|TOTAL DOS SERVICOS)"),
                ("net_amount", r"VALOR LIQUIDO(?: DA NOTA)?"),
            ):
                match = re.search(pattern + r"\s*:?\s*(?:R\$\s*)?([\d.]+,\d{2})\b", line)
                if match:
                    capture(field, fiscal_money(match[1].replace(".", "").replace(",", ".")), page_no, line_no)
            stamp = re.search(r"(?:DATA (?:DE )?EMISSAO|EMITIDA EM)\s*:?\s*(\d{2})/(\d{2})/(\d{4})", line)
            if stamp:
                capture("issued_on", fiscal_date(f"{stamp[3]}-{stamp[2]}-{stamp[1]}"), page_no, line_no)
            number = re.search(r"NUMERO (?:DA )?NOTA\s*:?\s*(\d+)", line)
            if number:
                capture("number", number[1], page_no, line_no)
            key = re.search(r"CHAVE DE ACESSO\s*:?\s*((?:\d[ .-]*){44})(?!\d)", line)
            if key and kind == "nfe":
                capture("identity_key", "nfe:" + re.sub(r"\D", "", key[1]), page_no, line_no)
            if re.search(r"\bNOTA (?:FISCAL )?CANCELADA\b|SITUACAO\s*:\s*CANCELADA", line):
                note["status"] = "cancelled"
    for field, value in values.items():
        if field.endswith("_tax"):
            note[field.removesuffix("_tax")]["tax_id"] = value
        else:
            note[field] = value
    return note

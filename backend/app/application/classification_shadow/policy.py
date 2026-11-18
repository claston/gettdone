"""Pure, versioned rules; no OCR, model calls or implicit fiscal defaults."""

import re
import xml.etree.ElementTree as ET
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text
from app.application.pro_fiscal_documents import NFE_NS, parse_fiscal_xml

RULE_VERSION = "financial-shadow-v1"
RECEITA_COMMIT = "150cccbe414d4a5edea703e4f7a424af5aa222c7"
FISCAL_VERSION = "receita-shadow-v1:" + RECEITA_COMMIT
LABELS = {
    "bank_fee": "Tarifa bancária", "interest": "Juros", "iof": "IOF",
    "tax_simples": "Simples Nacional / DAS", "tax_federal_unknown": "DARF — tributo a identificar",
    "tax_iss": "ISS", "tax_icms": "ICMS", "tax_irpj": "IRPJ", "tax_csll": "CSLL",
    "tax_pis": "PIS", "tax_cofins": "COFINS", "tax_social": "Contribuições previdenciárias",
}


def classify_movement(row):
    text = normalize_upper_text(row["description"])
    matches = []
    if Decimal(str(row["amount"])) < 0 and not re.search(r"\b(?:ESTORNO|DEVOLUCAO|REEMBOLSO)\b", text):
        for category, pattern in (("bank_fee", r"^TARIFA\b|^TAR\.?\s+BANCARIA\b"),
                                  ("interest", r"\bJUROS\b"), ("iof", r"^IOF\b|^DEBITO\s+IOF\b")):
            if re.search(pattern, text):
                matches.append(category)
        if re.search(r"^(?:PAGAMENTO|PAGTO|PGTO|ARRECADACAO|TRIBUTOS|IMPOSTO)\b", text):
            for category, pattern in (
                ("tax_simples", r"\bDAS\b|SIMPLES NACIONAL"), ("tax_federal_unknown", r"\bDARF\b"),
                ("tax_iss", r"\bISS(?:QN)?\b"), ("tax_icms", r"\bICMS\b"), ("tax_irpj", r"\bIRPJ\b"),
                ("tax_csll", r"\bCSLL\b"), ("tax_pis", r"\bPIS\b"), ("tax_cofins", r"\bCOFINS\b"),
                ("tax_social", r"\b(?:INSS|GPS)\b"),
            ):
                if re.search(pattern, text):
                    matches.append(category)
    category = matches[0] if len(matches) == 1 else None
    return {
        "id": str(row["id"]), "description": row["description"], "category": category,
        "label": LABELS.get(category, "Não determinado"), "status": "suggested" if category else "insufficient_data",
        "evidence": "Descrição e sinal do lançamento; confirmar natureza financeira." if category else
                    "Regras conflitantes." if matches else "A descrição não determina a natureza financeira.",
        "rule_version": RULE_VERSION, "candidates": [], "source_ref": row.get("provenance", {}),
    }


def extract_invoice_items(raw):
    note = parse_fiscal_xml(raw)  # Reuse size, namespace and entity protection.
    if note["kind"] != "nfe":
        raise ValueError("O piloto fiscal aceita itens de NF-e em XML. NFS-e ainda não é suportada.")
    ns = {"n": NFE_NS}
    info = next(ET.fromstring(raw).iter(f"{{{NFE_NS}}}infNFe"))

    def value(node, path):
        item = node.find("/".join("n:" + part for part in path.split("/")), ns)
        result = (item.text or "").strip() if item is not None else ""
        if len(result) > 600:
            raise ValueError("Campo do item fiscal excede o limite de 600 caracteres.")
        return result or None

    items, seen = [], set()
    for ordinal, element in enumerate(info.findall("n:det", ns), 1):
        number = element.get("nItem", "")
        if not re.fullmatch(r"[1-9]\d{0,3}", number) or number in seen:
            raise ValueError("Identificação de item ausente, inválida ou repetida no XML.")
        seen.add(number)
        items.append({
            "id": number, "ordinal": ordinal, "product_code": value(element, "prod/cProd"),
            "description": value(element, "prod/xProd"), "ncm": value(element, "prod/NCM"),
            "cfop": value(element, "prod/CFOP"), "quantity": value(element, "prod/qCom"),
            "unit": value(element, "prod/uCom"), "amount": value(element, "prod/vProd"),
            "cst": value(element, "imposto/IBSCBS/CST"), "cClassTrib": value(element, "imposto/IBSCBS/cClassTrib"),
            "source_ref": f"infNFe/det[@nItem='{number}']",
        })
    if not 1 <= len(items) <= 200:
        raise ValueError("O piloto aceita entre 1 e 200 itens por nota; nenhum item foi classificado.")
    return {"document_type": "NFe", "reference_date": note["issued_on"], "date_basis": "xml_issue_date",
            "regime": value(info, "emit/CRT"), "operation": note["operation"], "items": items}

"""Explainable PRO classifications and projections, independent of extraction."""

import re
from datetime import date
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text

CATEGORIES = {
    "operating_receipt": "Recebimento operacional",
    "operating_payment": "Pagamento operacional",
    "internal_transfer": "Transferência entre contas próprias",
    "loan": "Empréstimo",
    "capital": "Aporte de capital",
    "investment": "Aplicação ou resgate",
    "refund": "Estorno ou devolução",
    "tax_simples": "Simples Nacional / DAS",
    "tax_federal_unknown": "DARF — tributo a identificar",
    "tax_iss": "ISS",
    "tax_icms": "ICMS",
    "tax_irpj": "IRPJ",
    "tax_csll": "CSLL",
    "tax_pis": "PIS",
    "tax_cofins": "COFINS",
    "tax_social": "Contribuições previdenciárias",
    "tax_other": "Outros tributos",
    "unclassified": "Não classificado",
}


def classify_tax_payment(amount, description):
    if Decimal(amount) >= 0:
        return None
    text = normalize_upper_text(description)
    if not re.search(r"\b(?:PAGAMENTO|PAGTO|PGTO|ARRECADACAO|TRIBUTOS|IMPOSTO)\b", text):
        return None
    for category, pattern in (
        ("tax_simples", r"\bDAS\b|SIMPLES NACIONAL"),
        ("tax_federal_unknown", r"\bDARF\b"),
        ("tax_iss", r"\bISS(?:QN)?\b"),
        ("tax_icms", r"\bICMS\b"),
        ("tax_irpj", r"\bIRPJ\b"),
        ("tax_csll", r"\bCSLL\b"),
        ("tax_pis", r"\bPIS\b"),
        ("tax_cofins", r"\bCOFINS\b"),
        ("tax_social", r"\b(?:INSS|GPS)\b"),
    ):
        if re.search(pattern, text):
            return {
                "category": category,
                "state": "suggested",
                "evidence": "Descrição bancária; confirmar classificação.",
                "rule_version": "pro_tax_v1",
            }
    return None


def suggest_invoice_matches(note, movements):
    if note.get("review_required") or note.get("status") not in {"reported", "confirmed"} or not note.get("issued_on"):
        return []
    expected = note.get("net_amount") or note.get("gross_amount")
    if not expected or note.get("direction") not in {"issued", "received"}:
        return []
    party = note["recipient" if note["direction"] == "issued" else "issuer"]
    candidates = []
    for row in movements:
        value = Decimal(row["amount"])
        if (
            row["currency"] != note["currency"]
            or row.get("possible_duplicate")
            or (value > 0) != (note["direction"] == "issued")
            or abs(value) != Decimal(expected)
        ):
            continue
        gap = abs((date.fromisoformat(row["date"]) - date.fromisoformat(note["issued_on"])).days)
        if gap > 90:
            continue
        evidence = ["amount", "date_window"]
        description = normalize_upper_text(row["description"])
        if party.get("tax_id") and party["tax_id"] in re.sub(r"[^A-Z0-9]", "", description):
            evidence.append("counterparty_tax_id")
        if note.get("number") and re.search(r"\b(?:NF|NFE|NOTA)\s*[.:#-]?\s*0*" + re.escape(note["number"]) + r"\b", description):
            evidence.append("invoice_reference")
        candidates.append(
            {
                "movement_id": row["id"],
                "movement_version": row["version"],
                "amount": str(abs(value)),
                "movement": row,
                "state": "suggested",
                "evidence": evidence,
                "confidence": "strong" if len(evidence) > 2 else "weak",
            }
        )
    return sorted(candidates, key=lambda row: (-len(row["evidence"]), row["movement_id"]))[:20]


def possible_duplicate_note(first, second):
    def normalized(value):
        return str(value or "").strip().upper().lstrip("0") or "0"

    if not first.get("number") or not second.get("number") or not first["issuer"].get("tax_id"):
        return False
    return (
        first["kind"] == second["kind"]
        and normalized(first["number"]) == normalized(second["number"])
        and first["issuer"]["tax_id"] == second["issuer"]["tax_id"]
        and (not first.get("issued_on") or not second.get("issued_on") or first["issued_on"] == second["issued_on"])
        and (not first.get("series") or not second.get("series") or normalized(first["series"]) == normalized(second["series"]))
    )


def revenue_summary(notes, rows):
    uncertain = any(r.get("possible_duplicate") or r["currency"] != "BRL" for r in rows)
    receipts, pending, excluded = Decimal(0), Decimal(0), Decimal(0)
    taxes = {}
    for row in rows:
        amount = Decimal(row["amount"])
        classification = row.get("classification") or {}
        category = classification.get("category") if classification.get("state") == "confirmed" else "unclassified"
        if amount > 0:
            if category == "operating_receipt":
                receipts += amount
            elif category == "unclassified":
                pending += amount
            else:
                excluded += amount
        elif category.startswith("tax_"):
            taxes[category] = taxes.get(category, Decimal(0)) - amount
    sales = sum(
        (
            Decimal(n["gross_amount"])
            for n in notes
            if n.get("direction") == "issued"
            and n.get("status") in {"reported", "confirmed"}
            and n.get("operation") == "sale"
            and not n.get("review_required")
            and n.get("gross_amount")
        ),
        Decimal(0),
    )
    return {
        "documented_sales": format(sales, ".2f"),
        "operating_receipts": None if uncertain else format(receipts, ".2f"),
        "unclassified_inflows": None if uncertain else format(pending, ".2f"),
        "excluded_inflows": None if uncertain else format(excluded, ".2f"),
        "tax_payments": {key: None if uncertain else format(value, ".2f") for key, value in taxes.items()},
        "coverage": "unknown",
        "consolidation": "review_required" if uncertain else "observed",
        "pending_documents": sum(bool(n.get("review_required")) for n in notes),
        "basis": "Notas por emissão; recebimentos e tributos por data bancária. Totais independentes.",
    }

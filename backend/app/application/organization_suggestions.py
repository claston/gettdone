"""Suggestions from explicit persisted evidence, never from transaction counterparties."""

import re
import unicodedata
from datetime import date


def _date(value):
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _identifier(value):
    return re.sub(r"[^A-Za-z0-9]", "", _text(value)).upper()


def _name(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", _text(value))
                           if not unicodedata.combining(c)).casefold().split())


def build_source_evidence(analysis: dict) -> dict:
    """Capture once, before export-account settings or reviewed transactions can change."""
    financial = analysis.get("financial_document") or {}
    metadata = financial.get("metadata") or {}
    dates = sorted(filter(None, (_date(row.get("date")) for row in analysis.get("preview_transactions", [])
                                if isinstance(row, dict) and not row.get("is_deleted"))))
    start, end = _date(metadata.get("period_start")), _date(metadata.get("period_end"))
    period = None
    if start and end and start <= end:
        period = {"start_date": start, "end_date": end, "basis": "document_metadata", "document_coverage": True,
                  "evidence": "Intervalo identificado nos metadados do documento."}
    elif dates:
        period = {"start_date": dates[0], "end_date": dates[-1], "basis": "movement_dates", "document_coverage": False,
                  "evidence": "Datas dos lançamentos; sugestão de trabalho, não comprovação de cobertura documental."}
    tax = _identifier(metadata.get("owner_tax_id"))
    return {
        "version": 1,
        "holder_tax_id": tax if tax.isdigit() and len(tax) in (11, 14) else "",
        "holder_name": _text(metadata.get("owner_name")),
        "bank_code": _text(analysis.get("bank_code")), "branch": _text(analysis.get("bank_branch")),
        "account_number": _text(analysis.get("account_number")), "period": period,
    }


def suggest_organization(source: dict, catalog: dict) -> dict:
    clients, accounts = [], []
    tax, name = source.get("holder_tax_id"), _name(source.get("holder_name"))
    for client in catalog["clients"]:
        if tax:
            basis = "holder_tax_id" if tax == client.get("tax_id") else None
        else:
            basis = "holder_name" if name and name in {_name(client.get("name")), _name(client.get("trade_name"))} else None
        if basis:
            clients.append({"id": client["id"], "basis": basis,
                            "evidence": f"CPF/CNPJ do titular: {tax}" if tax else f"Nome do titular: {source['holder_name']}",
                            "confidence": "strong" if tax else "name"})
    bank, branch, number = (_identifier(source.get(field)) for field in ("bank_code", "branch", "account_number"))
    if bank and bank != "000" and branch and number:
        for account in catalog["accounts"]:
            owner = next((c for c in catalog["clients"] if c["id"] == account["client_id"]), None)
            if tax and owner and owner.get("tax_id") and owner["tax_id"] != tax:
                continue
            if (bank, branch, number) == tuple(_identifier(account.get(field))
                                               for field in ("bank_code", "branch", "account_number")):
                evidence = f"Conta de origem: banco {source['bank_code']}, agência {source['branch']}, conta {source['account_number']}."
                accounts.append({"id": account["id"], "client_id": account["client_id"], "basis": "source_account",
                                 "evidence": evidence, "confidence": "strong"})
                if not any(c["id"] == account["client_id"] for c in clients):
                    clients.append({"id": account["client_id"], "basis": "source_account", "evidence": evidence,
                                    "confidence": "strong"})
    for candidates in (clients, accounts):
        if len(candidates) > 1:
            for candidate in candidates:
                candidate["confidence"] = "ambiguous"
    return {"clients": clients, "accounts": accounts, "period": source.get("period"),
            "notice": "Sugestões não alteram vínculos. Confira a evidência e salve para confirmar."}

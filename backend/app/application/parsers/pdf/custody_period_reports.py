"""Custody and term-deposit snapshots remain observations, never invented cash dates."""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, fail, money


def _document(origin, evidence, observations, validations, metadata):
    notice = ("Relatório de investimentos: posições, proventos e resgates preservados no CSV e Excel, "
              "com as datas ou meses informados no documento. Os registros mantêm seu significado de investimento "
              "e não são projetados como lançamentos de conta corrente. OFX indisponível.")
    return {"schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": origin,
            "evidence": [evidence], "events": [], "observations": observations, "validations": validations,
            "metadata": metadata, "notice": notice,
            "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                       "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}


def parse_agora_custody(pages):
    text = upper("\n".join(pages))
    clients = set(re.findall(r"\bCLIENTE:\s*(\d+)\b", text))
    if len(clients) > 1:
        fail("O relatório de custódia contém titulares diferentes.")
    period = re.search(r"PERIODO DE:\s*(\d{2}/\d{4})", text)
    if not period or not 1 <= int(period[1][:2]) <= 12:
        fail("O período da custódia não está legível.")
    observations, checks, section = [], [], None
    headers = {"POSICAO EM CUSTODIA": "position", "PROVENTOS EM DINHEIRO - CREDITADOS": "credited",
               "PROVENTOS EM DINHEIRO - PROVISIONADOS": "provisioned", "MOVIMENTACAO DE ATIVOS": "movements"}
    empty_sections = set()
    for page, content in enumerate(pages, 1):
        for number, raw in enumerate(content.splitlines(), 1):
            normalized = upper(raw)
            if normalized in headers:
                section = headers[normalized]
                continue
            if "NAO FORAM ENCONTRADOS RESULTADOS" in normalized:
                empty_sections.add(section)
                continue
            if not section or normalized.startswith(("ATIVO ", "AGORA CORRETORA", "AV.", "SAO PAULO", "CNPJ:")) or not raw.strip():
                continue
            refs = [{"page": page, "line": number}]
            if section == "position":
                match = re.fullmatch(rf"\s*(.+?)\s+([A-Z]+\d+[A-Z]*)\s+(\d+)\s+({NUMBER})\s+({NUMBER})\s+(Cota|Ação|Acao)\s*", raw, re.I)
                if not match:
                    fail("Uma posição de custódia não pode ser associada ao ativo, quantidade e cotação.")
                name, code, quantity, quote, value, unit = match.groups()
                checks.append(_check(f"custody_p{page}_l{number}", Decimal(quantity)*money(quote), money(value), refs=refs))
                observations.append({"observation_type": "investment_position", "dates": {},
                    "amounts": {"gross": str(money(value))}, "attributes": {"asset": code, "label": name,
                    "quantity": quantity, "unit_quote": str(money(quote)), "unit": unit, "statement_month": period[1]},
                    "source_refs": refs})
            elif section in {"credited", "provisioned"}:
                pattern = rf"\s*(.+?)\s+([A-Z]+\d+[A-Z]*)\s+([A-Z]{{3}}/\d{{4}})\s+({NUMBER})\s*"
                match = re.fullmatch(pattern, raw, re.I)
                if not match:
                    fail("Um provento mensal está sem ativo, mês ou valor legível.")
                name, code, month, value = match.groups()
                observations.append({"observation_type": "investment_monthly_income", "dates": {},
                    "amounts": {"income": str(money(value))}, "attributes": {"asset": code, "label": name,
                    "credit_month": month, "printed_status": section}, "source_refs": refs})
            else:
                fail("A movimentação de ativos exige identificação própria; não pode ser convertida em caixa.")
    if not observations or "movements" not in empty_sections:
        fail("A cobertura da seção de movimentação de ativos não está confirmada.")
    return _document("Ágora", "agora_custody_monthly_positions", observations, checks, {"statement_month": period[1]})


def parse_fcbr76(pages):
    text = upper("\n".join(pages))
    accounts = set(re.findall(r"\bC/C:\s*([\d.-]+)", text))
    if len(accounts) > 1:
        fail("As aplicações pertencem a contas diferentes; não é possível combinar seus totais.")
    periods = set(re.findall(rf"PERIODO:\s*({DATE})\s+A\s+({DATE})", text))
    if len(periods) != 1:
        fail("O período das aplicações não está confirmado.")
    start, end = map(day, next(iter(periods)))
    observations, checks, columns, pending = [], [], None, None
    fields = ("principal", "opening_gross", "closing_gross", "period_income", "accumulated_income", "irrf", "gross_redemptions")
    totals = {}
    summary = None
    current_position = None
    redemption = None
    redemption_indents = {len(match[1]) for page in pages for line in page.splitlines()
                          if (match := re.match(r"^( +)RC\s+\d+\s+", line))}

    def values(raw, *, second=False):
        found = {}
        for match in re.finditer(NUMBER, raw):
            index = min(range(len(columns)), key=lambda i: abs(columns[i]-match.end()))
            if abs(columns[index]-match.end()) > 4 or fields[index] in found:
                fail("Uma coluna de aplicações CDB/RDB está deslocada ou ambígua.")
            field = fields[index]
            if second:
                field = {"principal": "printed_principal_complement", "irrf": "iof", "gross_redemptions": "net_redemptions"}.get(field)
                if field is None:
                    fail("Um complemento da aplicação está em coluna inesperada.")
            found[field] = str(money(match[0]))
        return found

    for page, content in enumerate(pages, 1):
        for line, raw in enumerate(content.splitlines(), 1):
            if line == 1 and raw.startswith("RC ") and len(redemption_indents) == 1:
                # Layout extraction strips the first line's leading whitespace.
                # Restore only the identical RC column witnessed on other rows.
                raw = " "*next(iter(redemption_indents))+raw
            normalized = upper(raw)
            if "VALOR APLICADO" in normalized and "RESGATE BRUTO" in normalized:
                headings = list(re.finditer(r"VALOR APLICADO|SALDO EM|REND\. BRUTO|\bIR\b|RESGATE BRUTO", raw))
                if len(headings) != 7:
                    fail()
                columns = [item.end() for item in headings]
                continue
            row = re.match(rf"^\s*(\d+)\s+({DATE})\s+({DATE})\s+", raw)
            redeemed = re.match(rf"^\s*RC\s+(\d+)\s+({DATE})\s+", raw)
            if redemption is not None:
                if not re.fullmatch(rf"\s*{NUMBER}(?:\s+{NUMBER})?\s*", raw):
                    fail("Um resgate está sem valor líquido ou impostos associados.")
                redemption["amounts"].update(values(raw, second=True))
                redemption["source_refs"].append({"page": page, "line": line})
                redemption["attributes"]["source_fields"]["Complemento impresso"] = raw.strip()
                amounts = redemption["amounts"]
                if "net_redemptions" not in amounts or "gross_redemptions" not in amounts:
                    fail("Um resgate está sem valor bruto ou líquido.")
                checks.append(_check(f"redemption_p{page}_l{line}",
                    Decimal(amounts["gross_redemptions"])-Decimal(amounts.get("irrf", "0"))-Decimal(amounts.get("iof", "0")),
                    Decimal(amounts["net_redemptions"]), refs=redemption["source_refs"]))
                observations.append(redemption)
                redemption = None
                continue
            if redeemed:
                if pending or current_position is None or not start <= day(redeemed[2]) <= end:
                    fail("Um resgate não está associado à aplicação e ao período.")
                redemption = {"observation_type": "investment_redemption", "dates": {"redemption": day(redeemed[2])},
                    "amounts": values(" "*redeemed.end()+raw[redeemed.end():]),
                    "attributes": {"reference": current_position, "redemption_reference": redeemed[1],
                                   "source_fields": {"Linha impressa": raw.strip()}},
                    "source_refs": [{"page": page, "line": line}]}
                continue
            if row:
                if pending or columns is None:
                    fail("Uma aplicação está sem seu complemento de taxa e valores.")
                amounts = values(" "*row.end()+raw[row.end():])
                pending = {"observation_type": "investment_position", "dates": {"application": day(row[2]), "maturity": day(row[3]),
                    "period_start": start, "period_end": end}, "amounts": amounts,
                    "attributes": {"reference": row[1], "source_fields": {"Linha impressa": raw.strip()}},
                    "source_refs": [{"page": page, "line": line}]}
                continue
            if columns and re.match(r"^\s*(?:SALDO EM CDB/RDB|TOTAL\s*\(1\s*\+\s*2\)|TOTAL\s{2,})", raw):
                summary = {"observation_type": "investment_summary", "dates": {"period_start": start, "period_end": end},
                    "amounts": values(raw), "attributes": {"label": re.split(r"\s{2,}", raw.strip())[0],
                                                          "source_fields": {"Linha impressa": raw.strip()}},
                    "source_refs": [{"page": page, "line": line}]}
                observations.append(summary)
                if re.match(r"^\s*TOTAL\s{2,}", raw):
                    totals = summary["amounts"]
                continue
            if summary and re.fullmatch(rf"\s*{NUMBER}(?:\s+{NUMBER})?\s*", raw):
                summary["amounts"].update(values(raw, second=True))
                summary["attributes"]["source_fields"]["Complemento impresso"] = raw.strip()
                summary["source_refs"].append({"page": page, "line": line})
                continue
            if pending:
                rate = re.match(r"^\s*(\d+,\d+)%\s+(\S+)", raw)
                if not rate:
                    fail("A taxa de uma aplicação CDB/RDB está ausente.")
                complement = values(" "*rate.end()+raw[rate.end():], second=True)
                # This unlabeled second printed principal is preserved verbatim;
                # do not claim it is a new application or a bank movement.
                printed = complement.pop("printed_principal_complement", None)
                pending["amounts"].update(complement)
                pending["attributes"].update(rate_percent=rate[1], index=rate[2], printed_principal_complement=printed)
                pending["attributes"]["source_fields"]["Complemento impresso"] = raw.strip()
                pending["source_refs"].append({"page": page, "line": line})
                observations.append(pending)
                current_position = pending["attributes"]["reference"]
                pending = None
                continue
            if columns and re.match(r"^\s*(?:RC\b|\d+\s+\d{2}/)", raw):
                fail("Há uma linha financeira não reconhecida no quadro de aplicações.")
    if pending or redemption or not observations or not totals:
        fail("Faltam aplicações, complementos ou totais do relatório CDB/RDB.")
    for field, total in totals.items():
        checks.append(_check(f"fcbr76_{field}", sum((Decimal(o["amounts"].get(field, "0")) for o in observations
                            if o["observation_type"] != "investment_summary"), Decimal(0)), Decimal(total)))
    metadata = {"period_start": start, "period_end": end}
    if accounts:
        metadata["source_account_number"] = next(iter(accounts))
    return _document("BRB", "brb_fcbr76_positions", observations, checks, metadata)

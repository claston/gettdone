"""Typed portfolio observations and collection postings, without invented balances."""

import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.custody_period_reports import _document
from app.application.parsers.pdf.financial_reports import DATE, MONEY, _check, _event, date, money
from app.application.parsers.pdf.verified_statements import fail


def parse_portfolio(pages):
    text = "\n".join(pages)
    normalized = upper(text)
    periods = set(re.findall(rf"PERIODO\s*:?\s*({DATE})\s+ATE\s+({DATE})", normalized))
    if len(periods) != 1:
        fail("O período do portfólio não está legível ou contém contextos diferentes.")
    start, end = map(date, next(iter(periods)))
    if start > end:
        fail("O período do portfólio é inválido.")
    accounts = set(re.findall(r"AGENCIA/CONTA\s+AG\s+([\dXx.-]+)\s+CC\s+([\d.-]+)", normalized))
    if len(accounts) > 1:
        fail("O portfólio contém contas diferentes; preserve os relatórios separadamente.")
    observations, checks = [], []
    section = None
    fields = {
        "classes": ("opening_gross", "period_inflows", "period_outflows", "closing_gross", "provision_tax", "closing_net"),
        "assets": ("opening_gross", "period_inflows", "period_outflows", "closing_gross", "provision_tax", "closing_net"),
        "liquidity": ("gross", "blocked", "available", "available_net"),
        "income": (
            "period_income",
            "day_income",
            "month_income",
            "previous_month_income",
            "three_month_income",
            "six_month_income",
            "year_income",
        ),
    }
    for page, content in enumerate(pages, 1):
        for line, raw in enumerate(content.splitlines(), 1):
            # Embedded fonts may split digits around thousands/decimal separators.
            raw = re.sub(r"(?<=\d)\s+(?=[.,]\s*\d)|(?<=[.,])\s+(?=\d)", "", raw)
            label = upper(raw)
            if "POSICAO DA CARTEIRA" in label:
                section = "classes"
            elif "DISTRIBUICAO DA CARTEIRA" in label:
                section = "assets"
            elif "LIQUIDEZ DA CARTEIRA" in label:
                section = "liquidity"
            elif "DETALHAMENTO DA RENTABILIDADE POR CLASSE" in label:
                section = "rates"
            elif "RENT" in label and "(EM R$)" in label:
                section = "income"
            elif "INFORMACOES RELEVANTES" in label:
                section = None
            if section == "rates" and re.match(r"\s*(?:CDI|%\s*CDI)\b", label):
                observations.append(
                    {
                        "observation_type": "investment_benchmark",
                        "dates": {"period_start": start, "period_end": end},
                        "amounts": {},
                        "attributes": {"label": raw.strip(), "source_fields": {"linha_impressa": raw.strip()}},
                        "source_refs": [{"page": page, "line": line}],
                    }
                )
                continue
            if not section or not re.search(r"R\$\s*(?:[\d+?-]|$)", raw):
                continue
            matches = list(re.finditer(rf"R\$\s*({MONEY})", raw))
            if not matches:
                fail("Uma linha financeira do portfólio contém valores ilegíveis.")
            row_label = raw[: matches[0].start()].strip()
            if not row_label or row_label.startswith(("SALDO", "Saldo")):
                continue
            values = [money(m[1]) for m in matches]
            percentages = re.findall(r"([+-]?[\d.,]+)\s*%", raw[matches[-1].end() :])
            refs = [{"page": page, "line": line}]
            if section in fields:
                roles = fields[section]
                if len(values) != len(roles):
                    fail("Uma linha do portfólio não corresponde às colunas monetárias impressas.")
                amounts = dict(zip(roles, values, strict=True))
            else:
                if len(values) != 1:
                    fail("A tabela de rentabilidade possui valores monetários ambíguos.")
                amounts = {"gross": values[0]}
            attrs = {"label": row_label, "section": section, "source_fields": {"linha_impressa": raw.strip()}}
            if percentages:
                attrs["rate_percent" if section == "classes" else "participation_percent"] = percentages[-1]
            observations.append(
                {
                    "observation_type": "investment_position" if section in {"classes", "assets"} else "investment_" + section,
                    "dates": {"period_start": start, "period_end": end},
                    "amounts": amounts,
                    "attributes": attrs,
                    "source_refs": refs,
                }
            )
            if section in {"classes", "assets"}:
                checks.append(
                    _check(
                        f"portfolio_net_p{page}_l{line}",
                        Decimal(amounts["closing_gross"]) - Decimal(amounts["provision_tax"]),
                        Decimal(amounts["closing_net"]),
                        refs=refs,
                    )
                )
    if not any(o["attributes"].get("section") == "classes" for o in observations):
        fail("As posições do portfólio não puderam ser lidas integralmente.")
    return _document(
        "Banco do Brasil", "portfolio_positions_and_performance", observations, checks, {"period_start": start, "period_end": end}
    )


def parse_collection_postings(pages):
    text = "\n".join(pages)
    periods = set(re.findall(rf"PERIODO LANCAMENTO:\s*({DATE})\s+A\s+({DATE})", upper(text)))
    if len(periods) != 1:
        fail("O período dos lançamentos de cobrança não está legível.")
    start, end = map(date, next(iter(periods)))
    if start > end:
        fail("O período dos lançamentos é inválido.")
    events, checks = [], []
    status = direction = None
    accounts = set()
    for page, content in enumerate(pages, 1):
        for line, raw in enumerate(content.splitlines(), 1):
            normalized = upper(raw)
            if normalized.startswith("SITUACAO:"):
                status = normalized.split(":", 1)[1].strip()
            if normalized.startswith("HISTORICO:"):
                direction = normalized.split(":", 1)[1].strip()
            account = re.search(r"BENEFICIARIO.*?\bCONTA:\s*([\d.-]+)", normalized)
            if account:
                accounts.add(re.sub(r"\D", "", account[1]))
            dates = list(re.finditer(DATE, raw))
            values = list(re.finditer(rf"R\$\s*({MONEY})", raw))
            refs = [{"page": page, "line": line}]
            if len(dates) == 3:
                if len(values) != 2 or status != "SUCESSO" or direction not in {"CREDITO", "DEBITO"}:
                    fail("Uma cobrança está sem valor, situação ou direção confirmada.")
                due, paid, booked = [date(m[0]) for m in dates]
                if not start <= booked <= end:
                    fail("A data de lançamento está fora do período impresso.")
                prefix = raw[: dates[0].start()].strip()
                identity = re.match(r"^(\d+)\s+(.+?)\s+(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\s+(\d+)\s*(.*)$", prefix)
                if not identity:
                    fail("Não foi possível associar os identificadores da cobrança.")
                nominal, amount = [money(m[1]) for m in values]
                signed = Decimal(amount) * (-1 if direction == "DEBITO" else 1)
                events.append(
                    _event(
                        "collection_posting",
                        dates={"due": due, "payment": paid, "transaction": booked},
                        amounts={"original": nominal, "settled": amount},
                        primary="settled",
                        cash_effect=str(signed),
                        reference=identity[4],
                        description="Lançamento de cobrança — " + identity[2],
                        refs=refs,
                        counterparty=identity[2],
                        attributes={
                            "tax_id": identity[3],
                            "contract": identity[1],
                            "customer_reference": identity[5],
                            "printed_status": status,
                            "direction": direction,
                        },
                    )
                )
            elif re.match(r"^\s*\d+\s+", raw) and (dates or values):
                fail("Uma linha de cobrança não contém as três datas e os dois valores esperados.")
            elif normalized.startswith("TOTAL") and values:
                count = re.search(r"(\d+)\s*BOLETO", normalized)
                if "TOTAL GERAL" in normalized or normalized.startswith("TOTAL R$"):
                    checks.append(
                        _check("collection_paid_total", sum(Decimal(e["amount"]) for e in events), Decimal(money(values[-1][1])), refs=refs)
                    )
                    if count:
                        checks.append(_check("collection_count", len(events), int(count[1]), refs=refs))
    if not events or len(accounts) > 1:
        fail("O relatório está vazio ou mistura contas distintas.")
    if not checks:
        fail("Falta o total geral para conferir a cobertura das cobranças.")
    notice = "Cobranças lançadas em conta: vencimento, pagamento e lançamento preservados. Valor pago não representa saldo bancário."
    return {
        "schema_version": "financial_events_v1",
        "document_type": "receivables_report",
        "origin": "Sicoob",
        "evidence": ["analytic_collection_postings"],
        "events": events,
        "observations": [],
        "validations": checks,
        "metadata": {"period_start": start, "period_end": end},
        "notice": notice,
        "export": {
            "available_formats": ["csv", "xlsx"],
            "ofx_profile": None,
            "requires_destination_account": False,
            "supports_row_edits": False,
            "reason": notice,
        },
    }

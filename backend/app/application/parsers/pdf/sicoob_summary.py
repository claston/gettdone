"""SISBR REM116 contains period aggregates and positions, not dated bank entries."""

import calendar
import re
from datetime import date

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, fail, money


def parse_sicoob_summary(pages):
    if len(pages) != 1:
        fail("Envie um único extrato consolidado Sicoob completo por arquivo.")
    text = normalize_upper_text(pages[0])
    period = re.search(r"PERIODO:\s*([A-Z]+)/([0-9]{4})", text)
    names = ("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO")
    if not period or period[1] not in names:
        fail()
    year, month = int(period[2]), names.index(period[1]) + 1
    start, end = date(year, month, 1).isoformat(), date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    labels = {
        "SALDO BRUTO ANTERIOR EM": "opening_gross",
        "APLICACOES:": "applications",
        "RENDIMENTOS BRUTO:": "gross_income",
        "RESGATES LIQUIDOS:": "net_redemptions",
        "IMPOSTO DE RENDA RETIDO:": "withheld_irrf",
        "IOF RETIDO:": "withheld_iof",
        "RENDIMENTO LIQUIDO:": "net_income",
        "SALDO BRUTO EM": "closing_gross",
        "IMPOSTO DE RENDA PREVISTO:": "expected_irrf",
        "IOF PREVISTO:": "expected_iof",
        "SALDO LIQUIDO EM": "closing_net",
    }
    observations, values = [], {}
    for number, line in enumerate(pages[0].splitlines(), 1):
        normalized = normalize_upper_text(line)
        for label, field in labels.items():
            if not normalized.startswith(label):
                continue
            match = re.search(rf"({NUMBER})\s*$", normalized)
            if not match or field in values:
                fail()
            value = money(match[1])
            values[field] = value
            printed_date = re.search(DATE, normalized)
            dates = {"period_start": start, "period_end": end}
            if printed_date:
                dates["as_of"] = day(printed_date[0])
                if field.startswith("closing") and not start <= dates["as_of"] <= end:
                    fail()
                if field == "opening_gross" and dates["as_of"] >= start:
                    fail()
            observations.append(
                {
                    "observation_type": "period_summary",
                    "dates": dates,
                    "amounts": {field: format(value, ".2f")},
                    "attributes": {"label": line[: match.start()].strip()},
                    "source_refs": [{"page": 1, "line": number}],
                }
            )
    if set(values) != set(labels.values()):
        fail("Não foi possível associar os campos do resumo Sicoob. Envie o PDF original completo.")
    gross = (
        values["opening_gross"]
        + values["applications"]
        + values["gross_income"]
        - values["net_redemptions"]
        - values["withheld_irrf"]
        - values["withheld_iof"]
    )
    net = values["closing_gross"] - values["expected_irrf"] - values["expected_iof"]
    if (gross, net) != (values["closing_gross"], values["closing_net"]):
        fail("Os saldos do resumo Sicoob não fecham com os valores impressos.")
    issue = re.search(rf"DATA EMISSAO:\s*({DATE})", text)
    notice = (
        "Resumo mensal de investimentos Sicoob: aplicações, resgates, rendimentos, impostos e posições agregados. "
        "O documento não informa as datas individuais das movimentações. Os dados estão preservados no CSV e Excel, "
        "sem criar transações bancárias na data de emissão. OFX indisponível para este resumo."
    )
    return {
        "schema_version": "financial_events_v1",
        "document_type": "investment_statement",
        "origin": "Sicoob",
        "evidence": ["sicoob_rem116_period_summary"],
        "events": [],
        "observations": observations,
        "metadata": {
            "period_start": start,
            "period_end": end,
            "product": "Captação Remunerada",
            "issue_date": day(issue[1]) if issue else None,
        },
        "validations": [
            {
                "code": code,
                "status": "passed",
                "actual": format(actual, ".2f"),
                "expected": format(expected, ".2f"),
                "source_refs": [ref for o in observations for ref in o["source_refs"]],
            }
            for code, actual, expected in (
                ("sicoob_gross_continuity", gross, values["closing_gross"]),
                ("sicoob_net_position", net, values["closing_net"]),
            )
        ],
        "notice": notice,
        "export": {
            "available_formats": ["csv", "xlsx"],
            "ofx_profile": None,
            "requires_destination_account": False,
            "supports_row_edits": False,
            "reason": notice,
        },
    }

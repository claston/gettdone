"""Native sales report: approval is not evidence of cash settlement."""
import re
from collections import defaultdict
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, fail, money


def parse_sales_pages(pages):
    text = upper("\n".join(pages))
    period = re.search(rf"PERIODO\s+({DATE})\s+A\s+({DATE})", text)
    if not period:
        fail()
    start, end = day(period[1]), day(period[2])
    events, checks = [], []
    totals, printed = defaultdict(lambda: Decimal(0)), {}
    current = None
    for page, content in enumerate(pages, 1):
        lines = content.splitlines()
        for number, raw in enumerate(lines, 1):
            if "VALOR LIQUIDO DO DIA:" in upper(raw):
                values = re.findall(rf"R\$\s*({NUMBER})", raw)
                if current is None or len(values) != 1 or current in printed:
                    fail()
                printed[current] = money(values[0])
                continue
            if not re.match(rf"^\s*{DATE}\s+\d{{2}}:", raw):
                continue
            cells = re.split(r"\s{2,}", raw.strip())
            if len(cells) != 10:
                fail("Uma venda está sem algum campo de identificação, situação ou valor.")
            stamp, acquirer, establishment, code, nsu, kind, status, gross, fee, net = cells
            stamp_match = re.fullmatch(rf"({DATE})\s+((?:[01]\d|2[0-3]):[0-5]\d)", stamp)
            if not stamp_match or upper(acquirer) != "PAGBANK" or not re.fullmatch(r"[A-Za-z0-9-]+", code):
                fail()
            date = day(stamp_match[1])
            if not start <= date <= end:
                fail()
            amounts = []
            for value in (gross, fee, net):
                match = re.fullmatch(rf"R\$\s*({NUMBER})", value)
                if not match:
                    fail()
                amounts.append(money(match[1]))
            gross_value, fee_value, net_value = amounts
            refs = [{"page": page, "line": number}]
            if upper(status) == "AGUARDANDO":
                continuation = next(((i, value.strip()) for i, value in enumerate(lines[number:], number + 1) if value.strip()), None)
                if continuation is None or upper(continuation[1]) != "PAGAMENTO":
                    fail("A situação de uma venda está incompleta.")
                status += " " + continuation[1]
                refs.append({"page": page, "line": continuation[0]})
            if upper(status) not in {"APROVADA", "AGUARDANDO PAGAMENTO"}:
                fail("O relatório contém uma situação de venda ainda não suportada.")
            current = date
            totals[date] += net_value
            checks.append(_check(f"sale_net_p{page}_l{number}", gross_value - fee_value, net_value, refs=refs))
            events.append(_event("acquirer_sale", dates={"transaction": date},
                amounts=dict(zip(("gross", "fee", "net"), (format(value, ".2f") for value in amounts), strict=True)),
                primary="net", cash_effect=None, reference=code, description=kind,
                status="pending" if upper(status) == "AGUARDANDO PAGAMENTO" else "approved", refs=refs,
                attributes={"time": stamp_match[2], "acquirer": acquirer, "establishment": establishment,
                            "nsu": nsu, "printed_status": status}))
    if not events or set(totals) != set(printed):
        fail("Falta uma página ou um total diário no relatório de vendas.")
    checks.extend(_check(f"sales_daily_net_{date}", total, printed[date]) for date, total in sorted(totals.items()))
    events.sort(key=lambda event: (event["dates"]["transaction"], event["attributes"]["time"]))
    notice = ("Relatório de vendas: bruto, taxa, líquido, situação e identificadores estão preservados. "
              "A aprovação da venda não comprova crédito em conta. CSV e Excel disponíveis; OFX indisponível. "
              "Confira as validações e use o extrato de liquidação para apurar o caixa.")
    return {"schema_version": "financial_events_v1", "document_type": "acquirer_sales_report", "origin": "PagBank",
            "evidence": ["pagbank_sales_gross_fee_net_status_columns"], "representation": "native_text",
            "events": events, "observations": [], "validations": checks,
            "metadata": {"period_start": start, "period_end": end}, "notice": notice,
            "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                       "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}

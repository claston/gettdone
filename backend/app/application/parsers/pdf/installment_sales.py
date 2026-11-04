"""Sales installments retain gross amounts; card direction is not cash direction."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.financial_event_projection import project_financial_document
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.followup_ocr import _headers
from app.application.parsers.pdf.grouped_daily_cash import cash
from app.application.parsers.pdf.native_table_source import native_header_columns, split_native_columns
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.parsers.pdf.verified_statements import DATE, day, fail
from app.application.source_regions import evidence_regions


def parse_installment_sales(source, pages, *, native=False):
    keys = tuple(map(upper, ("DATA DA TRANSACAO", "Nº DA TRANSACAO", "BANDEIRA", "PARCELA", "TIPO CARTAO",
            "NUMERO DO TERMINAL", "INDICADOR", "Nº ORDEM DE", "STATUS", "VALOR PARCELA BRUTO")))
    events, regions, totals, columns = [], [], [], None
    total_refs, establishments = [], []
    for page in source.pages:
        if not page.lines or any(not line.bbox for line in page.lines):
            fail("Uma página do relatório está sem as coordenadas necessárias para associar as parcelas.")
        found = native_header_columns(page, keys)
        if not found:
            # Native text can join terminal and indicator labels into one LINE.
            pair = native_header_columns(page, keys[5:7])
            rest = _headers(page, keys[:5]+keys[7:])
            if pair and rest:
                found = {**pair, **rest}
        columns = found or columns
        if columns is None:
            fail("Não foi possível separar as colunas do relatório de vendas.")
        boundaries = [columns[key]-.012 for key in keys[1:]]
        page = split_native_columns(page, boundaries)
        headers = [line for line in page.lines if upper(line.text) == keys[0]]
        tolerance = max(.003, min(line.bbox["height"] for line in page.lines if line.bbox)*.65)
        for label in page.lines:
            if upper(label.text).startswith("ESTABELECIMENTO(S):"):
                value = " ".join(line.text for line in page.lines
                    if abs(line.bbox["top"]-label.bbox["top"]) < tolerance)
                if value not in establishments:
                    establishments.append(value)
        top = max((line.bbox["top"]+.03 for line in headers), default=0)
        footers = [line.bbox["top"] for line in page.lines if re.match(r"(?:EMITIDO EM|PAGINA)\b", upper(line.text))]
        bottom = min(footers, default=1)-tolerance
        lines = [line for line in page.lines if top < line.bbox["top"] < bottom]
        anchors = sorted((line for line in lines if line.bbox["left"] < boundaries[0]),
                         key=lambda line: line.bbox["top"])
        used = set()
        for index, anchor in enumerate(anchors):
            stop = anchors[index+1].bbox["top"]-tolerance if index+1 < len(anchors) else bottom
            cells = [line for line in lines if anchor.bbox["top"]-tolerance <= line.bbox["top"] < stop]
            fields = [" ".join(line.text for line in sorted(cells, key=lambda c: c.bbox["top"])
                if sum(line.bbox["left"] >= b for b in boundaries) == col) for col in range(10)]
            if upper(anchor.text) == "TOTAL":
                if any(fields[1:9]) or not fields[9]:
                    fail("O total do relatório está ambíguo.")
                totals.append(cash(fields[9]))
                total_refs.extend({"page": page.page_number, "line": line.line_index} for line in cells)
                used.update(line.id for line in cells)
                continue
            stamp = re.fullmatch(rf"({DATE}) ((?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d)", fields[0])
            if not stamp or not all(fields) or not re.fullmatch(r"\d+", fields[3]):
                fail("Uma parcela está sem data, identificação, situação ou valor legível.")
            value = cash(fields[9])
            refs = [{"page": page.page_number, "line": number} for number in dict.fromkeys(c.line_index for c in cells)]
            attributes = dict(zip(("time", "transaction_id", "card_brand", "installment", "card_type",
                                  "terminal", "card_indicator", "payment_order", "printed_status"), fields[:9], strict=True))
            attributes["time"] = stamp[2]
            attributes["source_fields"] = dict(zip(keys, fields, strict=True))
            events.append(_event("acquirer_sale", dates={"transaction": day(stamp[1])},
                amounts={"gross": format(value, ".2f")}, primary="gross", cash_effect=None,
                reference=fields[1], description=f"{fields[2]} | {fields[4]} | Parcela {fields[3]}",
                status="reported", refs=refs, attributes=attributes))
            regions.append(evidence_regions(page.page_number, cells, words=page.words))
            used.update(line.id for line in cells)
        if not anchors or any(line.id not in used for line in lines):
            fail("Uma página contém campos sem parcela ou total associado.")
    if not events:
        fail("Nenhuma parcela legível foi identificada.")
    total = sum((Decimal(event["amount"]) for event in events), Decimal(0))
    checks = ([_check("sales_installments_gross", total, totals[0], refs=total_refs)] if len(totals) == 1 else
              [{"code": "sales_total_unconfirmed", "status": "failed", "actual": str(total),
                "expected": None, "source_refs": []}])
    if validate_statement_completeness(pages):
        checks.append({"code": "sales_page_coverage", "status": "failed", "actual": None,
                       "expected": "complete", "source_refs": []})
    notice = ("Relatório de vendas: parcelas, valores brutos, situação e identificadores preservados. "
              "Crédito/débito identifica a modalidade do cartão, não um movimento em conta. "
              "CSV e Excel disponíveis; OFX indisponível sem comprovação de liquidação.")
    document = {"schema_version": "financial_events_v1", "document_type": "acquirer_sales_report",
        "origin": None, "evidence": ["sales_gross_installment_columns"], "events": events,
        "observations": [], "validations": checks, "metadata": {"reported_establishments": "\n".join(establishments)}, "notice": notice,
        "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                   "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}
    result = project_financial_document(document, pages, native=native)
    return replace(result, canonical_transactions=[replace(row, source_regions=region)
        for row, region in zip(result.canonical_transactions, regions, strict=True)],
        source_extraction=source, source_layout_lines=tuple(tuple(page.lines) for page in source.pages))

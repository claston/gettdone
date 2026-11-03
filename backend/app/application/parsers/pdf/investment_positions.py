"""Named investment positions retain their as-of date and non-cash meaning."""
import re

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, fail, money

EVIDENCE = "sicoob_sisbr_fixed_income_positions"
FIELDS = {
    "VALOR APLICADO": "principal", "VALOR BRUTO": "gross", "VALOR LIQUIDO": "net",
    "RENDIMENTOS": "income", "IOF PREVISTO": "expected_iof", "IR PREVISTO": "expected_irrf",
    "VALOR BLOQUEADO": "blocked", "VALOR DISPONIVEL PARA RESGATE": "available_redemption",
}


def parse_fixed_income_positions(pages):
    observations, validations, position_dates = [], [], []
    for page, content in enumerate(pages, 1):
        text = normalize_upper_text(content)
        position = re.search(rf"DATA POSICAO:\s*({DATE})", text)
        if not position:
            fail("A posição de investimento está sem data de posição legível; a data de emissão não pode substituí-la.")
        as_of = day(position[1])
        position_dates.append(as_of)
        values, refs, pending = {}, [], None
        for line, raw in enumerate(content.splitlines(), 1):
            normalized = normalize_upper_text(raw)
            label = next((label for label in FIELDS if normalized.startswith(label + ":")), None)
            if label:
                if pending or FIELDS[label] in values:
                    fail("Os campos da posição de investimento estão incompletos ou repetidos.")
                pending = label, [{"page": page, "line": line}]
                remaining = normalized[len(label) + 1:].strip()
            elif pending and normalized:
                remaining = normalized
                pending[1].append({"page": page, "line": line})
            else:
                continue
            if not remaining:
                continue
            match = re.fullmatch(rf"(?:R\$\s*)?([+-]?{NUMBER})", remaining)
            if not match:
                fail("Um valor da posição de investimento não está legível ou associado ao seu campo.")
            label, sources = pending
            field, value = FIELDS[label], money(match[1])
            values[field] = value
            refs.extend(sources)
            observations.append({"observation_type": "investment_position", "dates": {"as_of": as_of},
                "amounts": {field: format(value, ".2f")}, "attributes": {"label": label}, "source_refs": sources})
            pending = None
        if pending or set(values) != set(FIELDS.values()):
            fail("A página não contém todos os campos da posição de investimento. Confira o documento completo.")
        for code, expected, actual in (
            ("investment_gross_position", values["principal"] + values["income"], values["gross"]),
            ("investment_net_position", values["gross"] - values["expected_iof"] - values["expected_irrf"], values["net"]),
        ):
            validations.append({"code": code, "status": "passed" if actual == expected else "failed",
                "actual": format(actual, ".2f"), "expected": format(expected, ".2f"), "source_refs": refs})
    notice = ("Posições de renda fixa: valores aplicados, brutos, líquidos, rendimentos, impostos previstos, bloqueios "
              "e disponibilidade para resgate estão preservados com a data de posição no CSV e Excel. "
              "Esses valores não são lançamentos bancários; OFX indisponível para este relatório.")
    return {"schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": "Sicoob",
        "evidence": [EVIDENCE], "events": [], "observations": observations, "validations": validations,
        "metadata": {"product": "Renda fixa", "position_dates": ", ".join(dict.fromkeys(position_dates))}, "notice": notice,
        "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                   "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}

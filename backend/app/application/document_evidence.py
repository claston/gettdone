"""Document evidence never creates bank movements or asserts fiscal/payment verification."""

import re
from datetime import datetime

from app.application.document_type_classifier import BANK_RECEIPT, FISCAL_INVOICE, classify_document_type
from app.application.normalization.text import normalize_upper_text

EVIDENCE_TYPES = {BANK_RECEIPT, FISCAL_INVOICE}


def parse_evidence_pages(pages, *, document_type=None):
    from app.application.pdf_document_segmentation import segment_pdf_pages

    classification = classify_document_type("document.pdf", b"", extracted_text="\n".join(pages))
    kind = document_type or classification.document_type
    if kind not in EVIDENCE_TYPES:
        return None
    # Preserve all statement blocks in compound documents, including a receipt cover.
    if document_type is None and any(part["included_in_statement"] for part in segment_pdf_pages(pages).parts):
        return None
    observations = []
    for page_no, page in enumerate(pages, 1):
        lines = page.splitlines()
        for line_no, raw in enumerate(lines, 1):
            line = normalize_upper_text(raw)
            refs = [{"page": page_no, "line": line_no}]
            label, amount_key = None, None
            if kind == BANK_RECEIPT:
                if re.search(r"^VALOR(?: DA TRANSFERENCIA| TRANSFERIDO| DO PIX| DO PAGAMENTO| PAGO| COBRADO)?"
                             r"(?:\s*:|\s+R\$|\s+\d|$)", line):
                    label, amount_key = "Valor da operação", "value"
                elif line in {"DE", "PARA"}:
                    label = "Pagador" if line == "DE" else "Recebedor"
                elif re.search(r"\b(?:ID DA TRANSACAO|IDENTIFICADOR|AUTENTICACAO|CONTROLE)\b", line):
                    label = "Identificação da operação"
                elif re.search(r"\b(?:PAGADOR|RECEBEDOR|FAVORECIDO|BENEFICIARIO|DESTINATARIO|REMETENTE|CPF|CNPJ)\b", line):
                    label = "Participante informado"
                elif re.search(r"\b\d{1,2}(?:/\d{2}/\d{4}|\s+[A-Z]{3}\.?(?:\s+DE)?\s+\d{4})\b", line):
                    label = "Data informada"
            else:
                if re.search(r"VALOR (?:TOTAL DA NOTA|DOS SERVICOS|TOTAL DOS SERVICOS)", line):
                    label, amount_key = "Valor bruto", "gross"
                elif "VALOR LIQUIDO" in line:
                    label, amount_key = "Valor líquido", "net"
                elif re.search(r"\b(?:PRESTADOR|EMITENTE|TOMADOR|DESTINATARIO|CNPJ|CPF|RAZAO SOCIAL)\b", line):
                    label = "Participante informado"
                elif re.search(r"\b(?:NUMERO (?:DA )?NOTA|CHAVE DE ACESSO|CODIGO DE VERIFICACAO)\b", line):
                    label = "Identificação da nota"
                elif re.search(r"\b(?:EMISSAO|EMITIDA EM|COMPETENCIA)\b", line):
                    label = "Data informada"
            if label:
                # A field label and its value may occupy different physical lines.
                # Retain both references, without consuming an unrelated paragraph.
                next_line = next(((i + 1, value) for i, value in enumerate(lines[line_no:], line_no) if value.strip()), None)
                label_only = (
                    line in {"DE", "PARA", "ID DA TRANSACAO", "CONTROLE", "AUTENTICACAO DO COMPROVANTE"}
                    or amount_key
                    and not re.search(r"\d", line)
                )
                if label_only and next_line:
                    following = normalize_upper_text(next_line[1])
                    if not amount_key or re.fullmatch(r"(?:R\$\s*)?[\d.]+,\d{2}", following):
                        raw = raw.strip() + ": " + next_line[1].strip()
                        line = normalize_upper_text(raw)
                        refs.append({"page": page_no, "line": next_line[0]})
                money = re.search(r"(?<![\d.])([\d.]+,\d{2})(?!\d)", line) if amount_key else None
                dates = {}
                if label == 'Data informada':
                    matches = re.findall(r'\b\d{1,2}/\d{2}/\d{4}\b', raw)
                    if len(matches) == 1:
                        try:
                            dates['document_date'] = datetime.strptime(matches[0], '%d/%m/%Y').date().isoformat()
                        except ValueError:
                            pass
                observations.append(
                    {
                        "observation_type": "document_field",
                        "dates": dates,
                        "amounts": {amount_key: money[1].replace(".", "").replace(",", ".")} if money else {},
                        "attributes": {"label": label, "value": raw.strip()},
                        "source_refs": refs,
                    }
                )
    return {
        "schema_version": "financial_events_v1",
        "document_type": kind,
        "evidence": classification.evidence,
        "origin": None,
        "representation": "text",
        "extraction_method": "text",
        "metadata": {"review_required": True, "authority_status": "not_verified", "payment_status": "not_verified"},
        "events": [],
        "observations": observations,
        "validations": [],
        "notice": (
            "Confira os dados da transferência no comprovante. Este documento não acrescenta uma movimentação ao extrato."
            if kind == BANK_RECEIPT
            else ("Confira os dados da nota. O arquivo não confirma pagamento nem consulta à Receita. "
                  "A importação não acrescenta uma movimentação ao extrato.")
        ),
        "export": {
            "available_formats": ["csv", "xlsx"],
            "ofx_profile": None,
            "requires_destination_account": False,
            "supports_row_edits": False,
            "reason": "document_evidence_without_bank_movements",
        },
    }


def parse_evidence_result(pages, *, native=False):
    from app.application.office_scope import pro_documents_enabled

    if not pro_documents_enabled():
        return None
    document = parse_evidence_pages(pages)
    if document is None:
        return None
    from app.application.financial_event_projection import project_financial_document

    return project_financial_document(document, pages, native=native)

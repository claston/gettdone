"""Fictional inputs for signed ledgers, tracked invoice text and rotated OCR."""
import re
from dataclasses import replace
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction


def viacredi_pages():
    return ["""VIACREDI AV - EXTRATO ESPECIAL REF.20/10/2026 PAG 1
CONTA/DV: 12345-6 TITULAR: EMPRESA EXEMPLO
LIMITE DE CREDITO: 5.000,00 SALDO ANTERIOR: 100,00
DATA HISTORICO LIBER DOCUMENTO VALOR SALDO
03/08/26 DEBITO PIX - EXEMPLO 123456.789 150,00 D 50,00-
04/08/26 CREDITO PIX - EXEMPLO 123456.790 200,00 C 150,00
""", """VIACREDI AV - EXTRATO ESPECIAL REF.20/10/2026 PAG 2
05/08/26 CARTAO DEBITO - LOJA 10000000001 10,00 D
05/08/26 DEBITO PIX - EXEMPLO 123456.791 20,00 D 120,00
******** AVISO: ESTE EXTRATO SERA TARIFADO EM R$ 4,10 NESTA DATA. ********
VALOR BLOQUEADO JUDICIALMENTE R$: 0,00
"""]


def bb_zero_pages():
    return ["""Extrato de Conta Corrente
Cliente EXEMPLO
Agência: 0001 Conta: 12345-6
Lançamentos
Dia Lote Documento Histórico Valor
30/05/2025 Saldo Anterior 100,00 (-)
02/06/2025 13601 123 Cobrança de I.O.F. 10,00 (-)
IOF Saldo Devedor Conta
00/00/0000 13128 Saldo do dia 110,00 (-)
03/06/2025 13128 124 BB GIRO PRONAMPE 50,00 (-)
03/06/2025 13128 124 Estorno de Débito 50,00 (+)
00/00/0000 14175 Saldo do dia 110,00 (-)
04/06/2025 TED-Crédito em Conta
""", """Extrato de Conta Corrente
Cliente EXEMPLO
Dia Lote Documento Histórico Valor
14175 125 BANCO EXEMPLO EMPRESA 200,00 (+)
00/00/0000 13128 Saldo do dia 90,00 (+)
30/06/2025 13601 126 Cobrança de Juros 5,00 (-)
30/06/2025 S A L D O 85,00 (+)
Informações Adicionais
- Limite Ouro Empresarial 2.000,00 (+)
Total Aplicações Financeiras 999,00
"""]


def unicred_invoice_pages():
    return ["""UNICRED
REF.: jan/2026
ESTA É A FATURA DE JANEIRO
NO VALOR TOTAL DE R$ 80,00.
VENCIMENTO 08 JAN 2026
Pagamento mínimo R$ 10,00
LIMITES TOTAIS DE CRÉDITO DO SEU CARTÃO: R$ 1.000,00
Página 1 de 4
""", """UNICRED
RESUMO DA FATURA
TOTAL FATURA ANTERIOR R$ 100,00
PAGAMENTOS RECEBIDOS - R$ 105,00
DESPESAS/DÉBITOS + R$ 85,00
ENCARGOS R$ 0
SALDO TOTAL = R$ 80,00
SALDO DO ROTATIVO ANTERIOR - R$ 5,00
Página 2 de 4
""", """UNICRED
LANÇAMENTOS – VISA SIGNATURE BUSINESS 4000**.******.1234
CLIENTE EXEMPLO
DATA DESCRIÇÃO VALOR
20/mar LOJA Parcelamento 6/12 R$ 60,00
31/dez Anuidade - bonificação - R$ 5,00
31/dez Anuidade - parcela R$ 5,00
02/dez OUTRA LOJA R$ 20,00
07/dez Pagamento Recebido - R$ 100,00
TOTAL DE CLIENTE R$ 80,00
Página 3 de 4
""", """FALE CONOSCO
APP Cartão Unicred VISA
SAC Unicred 0800 647 2930
RECIBO DO PAGADOR
08/01/2026 Data de Vencimento
80.00 Valor Cobrado
Página 4 de 4
"""]


def caixa_manager(*, rotated=False):
    entries = [
        ("GERENCIADOR", .05, .03), ("CAIXA", .15, .03),
        ("Agência: 0001 Conta: 12345-6", .05, .07),
        ("Saldo anterior ao período solicitado", .05, .12), ("R$ 100,00 C", .85, .12),
        ("Extrato no período de 01/09/2026 à 30/09/2026", .05, .16),
        ("Data", .05, .20), ("Data Efetiva", .05, .22), ("Documento Histórico", .20, .20),
        ("Valor", .65, .20), ("Saldo", .85, .20),
        ("08/09/2026", .05, .28), ("000000", .20, .29), ("DEBITO PRESTA SIEMP", .30, .29),
        ("R$ 30,00", .65, .29), ("R$ 70,00 C", .85, .29), ("05/09 02:23", .05, .31),
        ("08/09/2026", .05, .36), ("SALDO DIA", .30, .36), ("R$ 70,00 C", .85, .36),
        ("PIX RECEBIDO DADOS CONTA", .30, .40), ("09/09/2026", .05, .42),
        ("123456", .20, .43), ("EMPRESA EXEMPLO", .30, .43),
        ("R$ 50,00", .65, .43), ("R$ 120,00 C", .85, .43),
        ("09/09 12:37", .05, .45), ("09/09/2026", .05, .50),
        ("SALDO DIA", .30, .50), ("R$ 120,00 C", .85, .50), ("about:blank 1/2", .05, .95),
    ]
    lines = []
    for n, (text, x, y) in enumerate(entries, 1):
        box = dict(left=x, top=y, width=.06, height=.014)
        if rotated:
            box = dict(left=y, top=1-x-.06, width=.014, height=.06)
        lines.append(ExtractedLine(id=f"line-{n}", page_number=1, line_index=n, text=text, bbox=box))
    footer = ExtractedLine(id="footer", page_number=2, line_index=1, text="SAC CAIXA 0800 726 0101",
                           bbox=dict(left=.05, top=.10, width=.20, height=.014))
    return RawDocumentExtraction(provider="aws_textract", document_hash="fictional-caixa-manager", metrics={},
                                 pages=[ExtractedPage(page_number=1, lines=lines), ExtractedPage(page_number=2, lines=[footer])])


def mutate_ocr(extraction, old, new):
    return replace(extraction, pages=[replace(page, lines=[replace(line, text=line.text.replace(old, new))
                                                         for line in page.lines]) for page in extraction.pages])


def tracked_unicred_pdf(*, scale=1, missing_amount=False):
    """Draw monetary objects after all descriptions, with realistic tracking."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    for text in unicred_invoice_pages():
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        ops, delayed = [], []

        def cell(x, y, value):
            return f"BT /F1 8 Tf 0.9 Tc 1 0 0 1 {x} {y} Tm <{value.encode('cp1252').hex()}> Tj ET"

        for index, line in enumerate(text.splitlines()):
            y = 750-index*23
            match = re.fullmatch(r"(\d{2}/[a-z]{3})\s+(.+?)\s+(-?\s*R\$\s*[\d.,]+)", line)
            if match:
                ops.extend([cell(40, y, match[1]), cell(105, y, match[2])])
                if not missing_amount or "bonifica" not in match[2]:
                    delayed.append(cell(460, y, match[3]))
            else:
                ops.append(cell(40, y, line))
        stream = DecodedStreamObject()
        stream.set_data("\n".join([*ops, *delayed]).encode('ascii'))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page.scale_by(scale)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()

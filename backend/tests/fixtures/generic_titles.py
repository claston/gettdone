"""Fictional reports with unrelated column names, orders and arrangements."""
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from tests.fixtures.accounts_payable_pdf import accounts_payable_pdf
from tests.test_oct20_followup_ocr import ocr

NOTES = [
    ("Dt. Mov.", "movement", .03), ("Dt. Pag.", "payment", .13), ("Dt. Venc.", "due", .23),
    ("Número", "reference", .33), ("Descrição", "description", .41), ("Fornecedor", "party", .59),
    ("Título(R$)", "original", .77), ("Saldo a Pagar(R$)", "outstanding", .87),
]
REORDERED = [
    ("Cliente", "party", .03), ("Documento", "reference", .24), ("Vcto.", "due", .37),
    ("Valor original", "original", .49), ("Valor recebido", "settled", .63),
    ("Saldo a receber", "outstanding", .76), ("Status", "status", .9),
]
ROWS = [
    {"movement": "01/07/2026", "payment": "", "due": "10/07/2026", "reference": "0001/01",
     "description": "SERVIÇO TESTE", "party": "EMPRESA FICTÍCIA", "original": "100,00", "settled": "0,00",
     "outstanding": "100,00", "status": "Em aberto"},
    {"movement": "02/07/2026", "payment": "12/07/2026", "due": "11/07/2026", "reference": "0002/01",
     "description": "COMPRA TESTE", "party": "OUTRA EMPRESA", "original": "80,00", "settled": "50,00",
     "outstanding": "30,00", "status": "Parcial"},
]


def title_ocr(kind="payable", *, rows=None, reordered=False, total="180,00", split_pages=False):
    rows = ROWS if rows is None else rows
    title = "Notas a Pagar" if kind == "payable" else "Relatório de Contas a Receber"
    columns = REORDERED if reordered else NOTES
    groups = [[row] for row in rows] if split_pages else [rows]
    records = []
    for group in groups:
        page = [(title, .03, .02), ("Situação: A Pagar" if kind == "payable" else "Empresa Fictícia", .03, .05)]
        page += [(label, x, .12) for label, _, x in columns]
        for index, row in enumerate(group):
            page += [(row[key], x, .2 + index * .08) for _, key, x in columns if row.get(key)]
        page += [("Total geral", .03, .85), (total, next(x for _, key, x in columns if key == "original"), .85)]
        records.append(page)
    return ocr(records)


def title_pdf(kind="payable", *, rows=None, reordered=False, total="180,00", split_pages=False, rotation=0):
    from pypdf.generic import DecodedStreamObject

    source = title_ocr(kind, rows=rows, reordered=reordered, total=total, split_pages=split_pages)
    # Reuse only the fictional PDF's font/resources; draw the generic column grid.
    original = PdfReader(BytesIO(accounts_payable_pdf([], total="0,00", rotation=rotation)))
    writer = PdfWriter()
    for source_page in source.pages:
        page = writer.add_blank_page(width=1000, height=800)
        page[NameObject("/Resources")] = original.pages[0]["/Resources"].clone(writer)
        commands = []
        for line in source_page.lines:
            x, y = line.bbox["left"] * 1000, (1-line.bbox["top"]) * 800
            commands.append(f"BT /F1 7 Tf 1 0 0 1 {x} {y} Tm <{line.text.encode('cp1252').hex()}> Tj ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    payload = BytesIO()
    writer.write(payload)
    return payload.getvalue()

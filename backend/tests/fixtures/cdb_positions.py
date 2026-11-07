"""Fictional positions: four assets, the same assets detailed again, and printed totals."""

from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from tests.fixtures.recovery_acceptance import grid_pdf

ASSETS = [f"CDB000AAA0{i}" for i in range(1, 5)]


def cdb_cells():
    cells = [("Extrato da Conta Investimento", .02, .03), ("BTG Pactual", .8, .03),
             ("Período de 01/10/26 a 31/10/26", .02, .06)]
    for detail, top in [(False, .10), (True, .51)]:
        title = "Renda fixa - Detalhamento - CDB | BANCO SINTÉTICO" if detail else "Renda fixa - Posição - CDB"
        cells.append((title, .02, top))
        if detail:
            headers = [("Ativo", .025), ("Emissão", .16), ("Vencimento", .225), ("Aquisição", .30),
                ("Liquidez", .365), ("Dias de carência para", .41), ("Data inicial de", .46), ("Taxa", .52),
                ("Quantidade", .57), ("Preço Compra R$", .64), ("Valor Compra R$", .70),
                ("Preço R$", .76), ("Saldo Bruto R$", .815), ("IR R$", .865), ("IOF R$", .905), ("Saldo Líquido R$", .945)]
        else:
            headers = [("Emissor", .025), ("Ativo", .13), ("Emissão", .23), ("Vencimento", .295),
                ("Liquidez", .365), ("Dias de carência para", .41), ("Data inicial de", .46), ("Taxa Média", .52),
                ("Quantidade", .60), ("Preço R$", .70), ("Saldo Bruto R$", .78),
                ("IR R$", .85), ("IOF R$", .90), ("Saldo Líquido R$", .945)]
        cells.extend((text, x, top+.035) for text, x in headers)
        for index, asset in enumerate(ASSETS):
            y = top+.085+index*.055
            values = {"Ativo": asset, "Emissor": "BANCO TESTE", "Emissão": "01/09/26", "Vencimento": "01/09/28",
                "Aquisição": "02/09/26", "Liquidez": "Sim", "Dias de carência para": "1", "Data inicial de": "03/09/26",
                "Taxa": "CDI", "Taxa Média": "CDI", "Quantidade": "100,0000", "Preço Compra R$": "1,000000",
                "Valor Compra R$": "100,00", "Preço R$": "1,100000", "Saldo Bruto R$": "110,00",
                "IR R$": "2,00", "IOF R$": "-", "Saldo Líquido R$": "108,00"}
            cells.extend((values[label], x, y) for label, x in headers)
        totals = {"Valor Compra R$": "400,00", "Saldo Bruto R$": "440,00", "IR R$": "8,00", "IOF R$": "-",
                  "Saldo Líquido R$": "432,00"}
        cells.extend((totals[label], x, top+.315) for label, x in headers if label in totals)
    cells += [("Renda fixa - Posição Consolidada Por Emissor", .02, .89),
              ("Emissor", .02, .92), ("Saldo Bruto R$", .78, .92),
              ("BANCO TESTE", .02, .95), ("440,00", .78, .95)]
    return cells


def cdb_source(cells=None, *, provider="aws_textract"):
    lines, words = [], []
    for number, (text, x, y) in enumerate(cdb_cells() if cells is None else cells, 1):
        bbox = {"left": x, "top": y, "width": max(.004, len(text)*.0025), "height": .008}
        identifiers = []
        offset = 0
        for token in text.split():
            ident = f"w{len(words)+1}"
            word_box = {**bbox, "left": x+offset*.0025, "width": len(token)*.0025}
            words.append(ExtractedWord(ident, 1, len(words)+1, token, 99, word_box, "PRINTED"))
            identifiers.append(ident)
            offset += len(token)+1
        lines.append(ExtractedLine(f"l{number}", 1, number, text, 99, bbox, tuple(identifiers)))
    return RawDocumentExtraction(provider, "synthetic-cdb", [ExtractedPage(1, lines=lines, words=words)], {})


def cdb_pdf(cells=None):
    reader = PdfReader(BytesIO(grid_pdf([cdb_cells() if cells is None else cells])))
    writer = PdfWriter()
    for original in reader.pages:
        page = writer.add_page(original)
        stream = DecodedStreamObject()
        stream.set_data(page.get_contents().get_data().replace(b'/F1 8 Tf', b'/F1 4 Tf'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()

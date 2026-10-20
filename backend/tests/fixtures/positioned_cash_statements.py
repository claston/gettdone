"""Small synthetic statements; every field has an independent PDF text position."""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def positioned_pdf(pages, *, shuffled=False):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    for cells in pages:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        if shuffled:
            cells = sorted(cells, key=lambda item: (-item[0], item[1]))
        stream = DecodedStreamObject()
        stream.set_data("\n".join(f"BT /F1 8 Tf 1 0 0 1 {x} {y} Tm <{text.encode('cp1252').hex()}> Tj ET"
                                  for x, y, text in cells).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def grid(y=700, *, santander=False):
    return [(25, y, "Data"), (80, y, "Descrição" if santander else "Histórico"),
            (265, y, "Documento" if santander else "Docto"),
            (345, y-12 if santander else y, "Créditos" if santander else "Crédito"),
            (435, y-12 if santander else y, "Débitos" if santander else "Débito"),
            (525, y, "Saldo (R$)" if santander else "Saldo")]


def bradesco_pages(kind="unified"):
    title = "Extrato Unificado - Pessoa Jurídica" if kind == "unified" else "MOVIMENTAÇÃO DO PERÍODO - CONTA CORRENTE"
    return [[
        (25, 780, "Bradesco " + title), (25, 765, "Agência:1234 Conta:0000001-0"),
        (25, 750, "Período 01/09/2026 a 30/09/2026"),
        (25, 730, "Conta-Corrente Demonstrativo da Movimentação"), *grid(),
        (25, 675, "31/08" if kind == "unified" else "31/08/26"),
        (80, 675, "Saldo Anterior"), (540, 675, "1,00"),
        (25, 650, "01/09" if kind == "unified" else "01/09/26"),
        (80, 650, "RESGATE INVEST FACIL"), (270, 650, "000001"), (365, 650, "2,00"),
        (80, 625, "PAGAMENTO A FORNECEDORES"), (270, 625, "000002"), (355, 625, "200,00"),
        (80, 615, "CLIENTE EXEMPLO"),
    ], [
        *grid(760), (80, 730, "PIX ENVIADO"), (270, 730, "000003"), (445, 730, "202,00"),
        (80, 720, "DESTINATÁRIO EXEMPLO"), (540, 705, "1,00"),
        (25, 675, "Total" if kind == "unified" else "TOTAL DA MOVIMENTAÇÃO"),
        (355, 675, "202,00"), (445, 675, "202,00"), (540, 675, "1,00"),
        (25, 640, "Investimentos"), (80, 620, "Invest Fácil"), (540, 620, "7.000,00"),
    ], [(25, 760, "Investimentos CDB Bradesco"), (80, 700, "Saldo Anterior"), (540, 700, "10.000,00")]]


def santander_pages():
    return [[
        (25, 780, "Santander EXTRATO CONSOLIDADO INTELIGENTE fevereiro/2026"),
        (25, 765, "Extrato_PJ_A4_Inteligente"),
        (25, 745, "Resumo - fevereiro/2026"),
        (25, 725, "(=) Saldo de Conta Corrente em 31/01"), (400, 725, "0,00"),
        (25, 705, "(+) Total de Créditos"), (400, 705, "300,00"),
        (25, 685, "(-) Total de Débitos"), (400, 685, "300,00"),
        (25, 665, "(=) Saldo de Conta Corrente em 28/02"), (400, 665, "0,00"),
        (25, 640, "Conta Corrente Movimentação"), *grid(615, santander=True),
        (80, 580, "SALDO EM 31/01"), (540, 580, "0,00"),
        (25, 550, "04/02"), (80, 550, "PAGAMENTO A FORNECEDORES"),
        (270, 550, "010204"), (365, 550, "23,78"), (80, 540, "CLIENTE EXEMPLO"),
        (80, 515, "PAGAMENTO CARTAO DE DEBITO"), (270, 515, "000169"), (355, 515, "257,47"),
        (80, 505, "CREDENCIADORA EXEMPLO"),
    ], [
        *grid(750, santander=True),
        (80, 710, "PIX RECEBIDO"), (365, 710, "18,75"),
        (80, 685, "APLICACAO CONTAMAX"), (445, 685, "300,00-"), (540, 685, "0,00"),
        (80, 655, "SALDO EM 28/02"), (540, 655, "0,00"),
        (25, 625, "Saldos por Período"), (25, 600, "04/02"), (540, 600, "999,00"),
    ], [(25, 760, "Santander CDB"), (25, 700, "31/01 SALDO ANTERIOR"), (400, 700, "10.000,00")]]


def change_cells(pages, before, after):
    return [[(x, y, after if text == before else text) for x, y, text in cells] for cells in pages]

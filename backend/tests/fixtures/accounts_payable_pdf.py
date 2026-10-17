"""Generated, fictional payable grids; coordinates approximate ordinary reports."""
from io import BytesIO

from pypdf import PdfWriter, Transformation
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, RectangleObject

ROWS = [
    ("01/09/2026", "02/09/2026", "UBER SERVIÇO", "BANCO INTER FICTÍCIO", "Serviços", "90,00", "0,00", "Pago"),
    ("03/09/2026", "", "MANUTENÇÃO", "FORNECEDOR FICTÍCIO", "Serviços", "30,00", "30,00", "Vencido"),
    ("04/09/2026", "02/09/2026", "COMPRA PARCELADA", "FORNECEDOR FICTÍCIO", "Compras", "80,00", "20,00", "Pago parcialmente"),
]


def accounts_payable_pdf(rows=None, *, total="200,00", rotation=0, split_pages=False, missing_header=False, footer=False, paid=None):
    rows = ROWS if rows is None else rows
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    groups = [rows[:1], rows[1:]] if split_pages else [rows]
    for page_index, group in enumerate(groups):
        page = writer.add_blank_page(width=842, height=595)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        commands = []

        def cell(x, y, text):
            commands.append(f"BT /F1 7 Tf 1 0 0 1 {x} {y} Tm <{text.encode('cp1252').hex()}> Tj ET")

        if page_index == 0:
            cell(20, 565, "Relatório de Contas a Pagar")
            cell(20, 550, "EMPRESA FICTÍCIA")
            cell(600, 550, "Total do Período (R$)")
            cell(775, 550, total)
            if paid is not None:
                cell(600, 535, "Pagos (R$)")
                cell(775, 535, paid)
                cell(20, 520, "Há filtros aplicados")
        labels = ["Vencimento", "Pagamento", "Descrição", "Fornecedor", "Categoria", "Valor total", "Em aberto", "Situação"]
        xs = [20, 95, 170, 320, 435, 605, 690, 755]
        for x, label in zip(xs, labels, strict=True):
            if not (missing_header and label == "Em aberto"):
                cell(x, 500, label)
        for index, row in enumerate(group):
            y = 475 - index * 40
            for x, text in zip(xs, row, strict=True):
                for line_index, line in enumerate(text.splitlines()):
                    cell(x, y - line_index * 9, line)
        if footer:
            footer_y = 475 - max(0, len(group) - 1) * 40 - 25
            cell(20, footer_y, "Totais do período")
            cell(20, footer_y - 15, "De 01/09/2026 a 30/09/2026")
            cell(200, footer_y, "Total de registros")
            cell(200, footer_y - 15, str(len(group)))
            cell(20, 20, "Usuário: EMPRESA FICTÍCIA")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        if rotation == 90:
            page.add_transformation(Transformation((0, 1, -1, 0, 595, 0)))
            page.mediabox = RectangleObject([0, 0, 595, 842])
            page.rotate(90)
        elif rotation == 180:
            page.add_transformation(Transformation((-1, 0, 0, -1, 842, 595)))
            page.rotate(180)
        elif rotation == 270:
            page.add_transformation(Transformation((0, -1, 1, 0, 0, 842)))
            page.mediabox = RectangleObject([0, 0, 595, 842])
            page.rotate(270)
    if split_pages:
        writer.add_blank_page(width=842, height=595)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()

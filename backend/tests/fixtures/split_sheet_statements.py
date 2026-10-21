"""Invented records reproducing Excel column pagination and HTML icon signs."""

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def document(pages, *, scale=1, shuffled=False):
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    cmap = DecodedStreamObject()
    cmap.set_data(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def "
        b"/CMapName /Icons def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange "
        b"2 beginbfchar <2B> <F055> <2D> <F056> endbfchar endcmap "
        b"CMapName currentdict /CMap defineresource pop end end"
    )
    icon = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/ToUnicode"): writer._add_object(cmap),
            }
        )
    )
    for cells in pages:
        page = writer.add_blank_page(width=600 * scale, height=800 * scale)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font, NameObject("/F2"): icon})}
        )
        commands = [f"q {scale} 0 0 {scale} 0 0 cm"]
        for x, y, text in sorted(cells, key=lambda c: (-c[0], c[1])) if shuffled else cells:
            name = "F2" if text in ("\uf055", "\uf056") else "F1"
            text = text.replace("\uf055", "+").replace("\uf056", "-")
            commands.append(f"BT /{name} 8 Tf 1 0 0 1 {x} {y} Tm <{text.encode('cp1252').hex()}> Tj ET")
        commands.append("Q")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def bb_pages():
    return [
        [
            (50, 750, "Data"),
            (130, 750, "Lançamento"),
            (300, 750, "Detalhes"),
            (50, 720, "31/01/2026 Saldo Anterior"),
            (50, 700, "02/02/2026 BB Rende Fácil"),
            (300, 700, "Rende Facil"),
            (50, 680, "02/02/2026 Saldo do dia"),
        ],
        [
            (50, 740, "03/02/2026 Pix - Enviado"),
            (300, 740, "BENEFICIÁRIO FICTÍCIO"),
            (50, 720, "03/02/2026 Pix - Enviado"),
            (300, 720, "OUTRO BENEFICIÁRIO"),
            (50, 700, "03/02/2026 Saldo do dia"),
            (50, 680, "28/02/2026 S A L D O"),
        ],
        [
            (50, 750, "N° documento"),
            (170, 750, "Valor"),
            (250, 750, "Tipo Lançamento"),
            (190, 720, "10,00"),
            (110, 700, "9903"),
            (190, 700, "25,00"),
            (250, 700, "Entrada"),
            (190, 680, "35,00"),
        ],
        [
            (110, 740, "30301"),
            (190, 740, "-5,00"),
            (250, 740, "Saída"),
            (110, 720, "30302"),
            (190, 720, "-30,00"),
            (250, 720, "Saída"),
            (190, 700, "0,00"),
            (190, 680, "0,00"),
        ],
    ]


def billing_pages():
    header = [
        (25, 750, "Data"),
        (85, 750, "Transação"),
        (145, 750, "Tipo de transação"),
        (245, 750, "Transação estornada"),
        (345, 750, "Descrição"),
        (405, 750, "Valor"),
        (455, 750, "Saldo"),
        (505, 750, "Fatura do parcelamento"),
        (550, 750, "Fatura da cobrança"),
        (570, 750, "Nota fiscal"),
    ]
    return [
        [
            (25, 780, "Período a partir de 01/02/2026 até 28/02/2026"),
            *header,
            (345, 730, "Saldo Inicial"),
            (455, 730, "0"),
            (25, 700, "02/02/2026"),
            (85, 700, "1.86E+09"),
            (145, 700, "Cobrança recebida"),
            (345, 700, "PAGADOR FICTÍCIO"),
            (405, 700, "100"),
            (455, 700, "100"),
            (550, 700, "999999999"),
            (25, 680, "02/02/2026"),
            (85, 680, "1.87E+09"),
            (145, 680, "Taxa de boleto, cartão ou Pix"),
            (345, 680, "Taxa de boleto"),
            (405, 680, "-0.99"),
            (455, 680, "99.01"),
        ],
        [
            (25, 740, "03/02/2026"),
            (85, 740, "1.88E+09"),
            (145, 740, "Transação via Pix"),
            (345, 740, "DESTINATÁRIO FICTÍCIO"),
            (405, 740, "-20"),
            (455, 740, "79.01"),
            (345, 710, "Saldo Final"),
            (455, 710, "79.01"),
        ],
        [(25, 750, "Wallet"), (150, 750, "Tipo do lançamento"), (150, 700, "Crédito"), (150, 680, "Débito")],
        [(150, 740, "Débito")],
    ]


def amazon_pages():
    header = [
        (25, 780, "Banco da Amazônia S.A."),
        (25, 765, "CONSULTA DE EXTRATO"),
        (25, 750, "Extrato de Conta Corrente para Simples Conferência"),
        (25, 730, "Saldo Anterior:"),
        (515, 730, "R$ 0,00"),
        (565, 730, "\uf055"),
        (70, 710, "Data"),
        (150, 710, "Documento"),
        (220, 710, "Histórico"),
        (335, 710, "Detalhes da transação"),
        (465, 710, "Valor"),
        (530, 710, "Saldo"),
    ]

    def row(y, date, ref, description, amount, sign):
        return [(70, y, date), (150, y, ref), (220, y, description), (465, y, "R$ " + amount), (465.2, y, "R$ " + amount), (510, y, sign)]

    return [
        [
            *header,
            *row(680, "02/02/2026", "123", "ADQUIRENTE CART", "100,00", "\uf055"),
            *row(660, "02/02/2026", "124", "Transf CC p/CC", "20,00", "\uf056"),
            (335, 660, "Banco do Brasil"),
            (380, 640, "Saldo do dia"),
            (515, 640, "R$ 80,00"),
            (565, 640, "\uf055"),
        ],
        [
            *row(740, "03/02/2026", "125", "ADQ ELO CRED", "5,00", "\uf055"),
            (380, 720, "Saldo do dia"),
            (515, 720, "R$ 85,00"),
            (565, 720, "\uf055"),
            (25, 690, "Saldos"),
            (380, 670, "Saldo Atual"),
            (515, 670, "R$ 85,00"),
            (565, 670, "\uf055"),
            (380, 650, "= Saldo Disponível"),
            (515, 650, "R$ 85,00"),
            (565, 650, "\uf055"),
        ],
    ]


BUILDERS = {"bb": bb_pages, "billing": billing_pages, "amazon": amazon_pages}


def change(pages, before, after):
    return [[(x, y, after if t == before else t) for x, y, t in p] for p in pages]

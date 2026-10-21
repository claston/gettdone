"""Visually identical Sicoob statements with different content stream orders."""
from tests.fixtures.positioned_cash_statements import positioned_pdf


def sicoob_source_pdf(*, columns=True):
    cells = [
        (30, 780, "SICOOB SISTEMA DE COOPERATIVAS DE CREDITO DO BRASIL"),
        (30, 765, "PLATAFORMA DE SERVICOS FINANCEIROS DO SICOOB - SISBR"),
        (30, 750, "EXTRATO DE CONTA CORRENTE"),
        (30, 735, "Cooperativa: 0000 Conta: 00000-0 Periodo: 01/08/2026 - 31/08/2026"),
        (30, 715, "HISTORICO DE MOVIMENTACAO"), (30, 690, "Data Documento"),
    ]
    dates = ["03/08", "03/08", "03/08 Pix", "03/08 105045", "03/08 41-1", "03/08", "04/08 Pix", "04/08", "26/08 Pix", "26/08"]
    descriptions = ["SALDO ANTERIOR", "SALDO BLOQUEADO ANTERIOR", "PIX RECEBIDO", "PARCELAS SUBSC", "APLICACAO RDC",
                    "SALDO DO DIA", "TRANSFERENCIA POUPANCA", "SALDO DO DIA", "PIX RECEBIDO", "SALDO DO DIA"]
    amounts = ["R$ 0,00C", "R$ 0,00*", "R$ 500,00C", "R$ 100,00D", "R$ 100,00D", "R$ 300,00C", "R$ 100,00D",
               "R$ 200,00C", "R$ 8.625,00C", "R$ 8.825,00C"]
    cells += [(30, 675-i*20, text) for i, text in enumerate(dates)]
    cells += [(150, 690, "Historico"), *[(150, 675-i*20, text) for i, text in enumerate(descriptions)]]
    cells += [(150, 460, "Cheque especial contratado:"), (150, 445, "01/08/2026 - 31/08/2026")]
    cells += [(450, 690, "Valor"), *[(450, 675-i*20, text) for i, text in enumerate(amounts)]]
    return positioned_pdf([cells if columns else sorted(cells, key=lambda cell: (-cell[1], cell[0]))])

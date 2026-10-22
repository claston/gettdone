"""Synthetic exports: preserve document structure without production identities."""

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction


def sumup_pages():
    return [
        "\n".join([
            "Extrato bancário por período", "Documento gerado em 02/10/2026",
            "Nome do correntista", "EMPRESA TESTE", "CNPJ 00.000.000/0000-00", "Dados da conta",
            "Saldo em 30/09/2026 R$ 50,00", "Data e Hora Valor(R$) Tipo Descrição",
            "30/09/2026 20:23 30,00 Pix recebido COMERCIO TESTE",
            "30/09/2026 19:42 - 20,00 Pix enviado DESTINATARIO",
            "TESTE", "Extrato bancário de 01/09/2026 a 30/09/2026", "Agência: 0001 Conta: 123456",
        ]),
        "\n".join([
            "29/09/2026 11:00 40,00 Vendas SumUp", "29/09/2026 10:00 - 100,00 Pix enviado CONTA PROPRIA",
            "Ainda com dúvidas?", "4003-6338 0800 023 6510",
        ]),
    ]


def santander_pages():
    return [
        "\n".join([
            "Internet Banking Empresarial", "EMPRESA TESTE Agência: 1234 Conta: 123456",
            "Quarta, 30 de setembro de 2026", "APLICACAO CONTAMAX DEBITO R$ 100,00",
            "PAGAMENTO CARTAO DE CREDITO CREDITO R$ 100,00",
        ]),
        "\n".join([
            "Central de Atendimento Santander Empresarial 4004 2125", "SAC e Ouvidoria",
            "Sexta, 25 de setembro de 2026", "PIX RECEBIDO CREDITO R$ 50,00",
            "PIX ENVIADO DEBITO R$ 50,00",
        ]),
    ]


def cresol_inactive_pages():
    return ["\n".join([
        "EMPRESA TESTE", "Agência 1234 Conta 123456-7", "Saldo em Conta Limite de Crédito Saldo Disponível",
        "R$ 30,93 R$ 0,00 R$ 0,00", "01 de Setembro de 2026 a 30 de Setembro de 2026",
        "Lançamentos", "Nenhum lançamento encontrado", "+ R$ 30,93Saldo Anterior:",
        "Consulta Posição consolidada em 02/10/2026 às 08:07:33", "Periodo de 01/09/2026 a 30/09/2026",
        "Página 1 de 1",
    ])]


REPORT_HEADER = (f"{'Data':<18}{'Descrição':<44}{'Cliente/Fornecedor':<25}{'Categoria':<30}"
                 f"{'Situação':<20}{'Valor (R$)':>16}{'Saldo (R$)':>18}")


def report_row(date, description, counterparty, category, status, amount, balance):
    return (f"{date:<18}{description:<44}{counterparty:<25}{category:<30}"
            f"{status:<20}{amount:>16}{balance:>18}")


def conta_azul_pages():
    return ["\n".join([
        "Relatório de extrato 01/01/2026 a 31/01/2026", "EMPRESA TESTE",
        "Receitas Em Aberto (R$) 0,00", "Receitas Realizadas (R$) 120,00",
        "Despesas Em Aberto (R$) 0,00", "Despesas Realizadas (R$) 30,00",
        "Totais do Período (R$) 90,00", "Conta: Santander, Santander Aplicação", REPORT_HEADER,
        report_row("02/01/2026", "Pix recebido", "CLIENTE TESTE", "Receitas de Vendas", "Recebido", "120,00", "220,00"),
        report_row("03/01/2026", "Pagamento de serviço", "FORNECEDOR TESTE", "Serviços", "Pago", "- 30,00", "190,00"),
        f"{'':18}COMPLEMENTO DA DESCRICAO",
        "Totais do período Desconsiderados Perdidos Saldo Realizado", "R$ 0,00 R$ 0,00 R$ 190,00",
        "Usuário: Contador Página 1 de 1",
    ])]


def bradesco_monthly_extraction():
    entries = [
        ("bradesco", .05, .02), ("Extrato Mensal", .4, .04),
        ("REDE BRADESCO CONSULTA DE SALDO", .25, .06),
        ("AGENCIA 1234 CONTA 123456-7", .25, .08), ("Setembro/2026", .4, .10),
        ("DIA", .25, .12), ("HISTORICO", .32, .12), ("N.DOCTO", .48, .12), ("VALOR", .72, .12),
        ("SALDO ANTERIOR", .32, .14), ("100,00", .72, .14),
        ("11", .25, .16), ("PIX RECEBIDO", .32, .16), ("1234567", .48, .16), ("50,00", .72, .16),
        ("REM: COMERCIO TESTE 11/09", .32, .18),
        ("SALDO EM", .32, .20), ("11/09", .5, .20), ("150,00", .72, .20),
        ("15", .25, .22), ("COMPRA VISA", .32, .22), ("7654321", .48, .22), ("20,00-", .72, .22),
        ("ESTABELECIMENTO TESTE", .32, .24),
        ("SALDO EM", .32, .26), ("15/09", .5, .26), ("130,00", .72, .26),
        ("Página: 1 de 1", .6, .95),
    ]
    lines = [ExtractedLine(id=f"line-{i}", page_number=1, line_index=i, text=text, confidence=99,
                           bbox={"left": left, "top": top, "width": .1, "height": .01})
             for i, (text, left, top) in enumerate(entries, 1)]
    return RawDocumentExtraction(provider="aws_textract", document_hash="synthetic", metrics={},
                                 pages=[ExtractedPage(page_number=1, lines=lines)])

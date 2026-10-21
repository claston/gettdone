"""Fictional statements reproducing formatting defects, without customer data."""


def inter_page():
    return """Extrato Conta Corrente
Conta 12345678
Período 01/08/2026 a 31/08/2026
Saldo: 1,23
Data Lançamento Descrição Valor Saldo
03/08/2026 Pagamento efetuado: Cartao Inter -97 3,00 Cartão
04/08/2026 Pix recebido: Documento 12345678901234 990 993,00
05/08/2026 Pix enviado: Exemplo -270,9 722,10 Serviços
"""


def cresol_descending_pages():
    return ["""Agência 0001 Conta 12345-6
Saldo em Conta          Limite de Crédito          Saldo Disponível
- R$ 30,00              R$ 100,00                  R$ 70,00
01 de Agosto de 2026 a 31 de Agosto de 2026
Lançamentos
05/08/2026 Saldo do Dia: - R$ 30,00
05/08/2026 PACOTE DE SERVIÇOS - R$ 10,00
04/08/2026 Saldo do Dia: - R$ 20,00
04/08/2026 ESTORNO IOF + R$ 2,00
referencia-001
Consulta Posição consolidada em 15/10/2026 às 10:00:00
Periodo de 01/08/2026 a 31/08/2026
Página 1 de 2
""", """Lançamentos
04/08/2026 Saldo do Dia: - R$ 20,00
04/08/2026 IOF - R$ 2,00
03/08/2026 Saldo do Dia: - R$ 20,00
03/08/2026 VENDAS CARTAO + R$ 30,00
referencia-STONE
Saldo Anterior: - R$ 50,00
Consulta Posição consolidada em 15/10/2026 às 10:00:00
Periodo de 01/08/2026 a 31/08/2026
Página 2 de 2
"""]


def colmeia_pages():
    return ["""EXTRATO CONSOLIDADO DE CONTA CORRENTE
INSTITUICAO FINANCEIRA: 133
AGENCIA: 0001 CONTA: 12345-6
PERIODO: 01/08/2026 A 31/08/2026
================================
05/08/2026 SALDO ANT.: 1,00 C
TED CREDITO
(EXEMPLO)
100,00 C
PIX DEBITO
(PARA: EXEMPLO)
30,00 D
06/08/2026 SALDO ANT.: 71,00 C
PIX DEBITO
(PARA: EXEMPLO) 20,00 D
""", """21/10/2026, 12:00 Colmeia - Sistema de Gestão de Cooperativas de Crédito
PIX DEBITO
(PARA: EXEMPLO) 50,00 D
================================
(=) SALDO ....................: 1,00 C
(+) LIMITE DE CREDITO.........: 5.000,00 C
(=) SALDO DISPONIVEL P/SAQUE..: 5.001,00 C
07/11/2026 PGTO PARCELA EMPREST 2.000,00
CREDITO PRE-APROVADO R$ 10.000,00
CRESOL - INVISTA COM SEGURANÇA
"""]


def btg_investment_page():
    return """Extrato de Conta Investimento
Banco: 208 BTGPACTUAL
Período de 01/09/2026 a 30/09/2026
Movimentação - Conta Investimento
Data       Descrição                                Débito       Crédito        Saldo
01/09/2026 Saldo Inicial                                                       100,00
02/09/2026 AQUISICAO DE COTAS NO FUNDO CDB            100,05                      -0,05
03/09/2026 CONTA REMUNERADA - RESGATE REMUNERACAO                   0,05          0,00
30/09/2026 Saldo Final                                                           0,00
Total de Créditos                                                       0,05
Total de Débitos                                     100,05
"""


def alelo_page():
    return """Extrato Cartão - Despesas Corporativas
Cartão EMPRESA EXEMPLO - 030000000000000
Data do Extrato De: 01/09/2026 Até: 30/09/2026
Data Nome Número de série Tipo de Transação Estabelecimento Ramo Valor
29/09/26 CLIENTE EXEMPLO 030000000000000 Saque -R$ 1,000,00
16:43:00 Banco24Horas
28/09/26 CLIENTE EXEMPLO 030000000000000 Transação Compra LOJA -R$ 1,264,86
10:00:00 Comércio
11/09/26 CLIENTE EXEMPLO 030000000000000 Transferência de R$ 3.000,00
13:21:01 Saldo a Crédito
Saldo no dia 21/10/2026 11:49:33: R$ 100,00
ALELO - PORTAL PREPAX - CLIENTE
"""


def btg_invoice_pages():
    return ["""CLIENTE EXEMPLO | fatura de Janeiro de 2026 | cartão final 1234
BTG Pactual S.A
Resumo da Fatura Atual
Total de compras e despesas dessa fatura R$ 125,00
Total da fatura anterior R$ 30,00
Total de pagamentos feitos pelo cliente -R$ 30,00
Total a pagar R$ 125,00
Vencimento: 05/01
""", """CLIENTE EXEMPLO | fatura de Janeiro de 2026 | cartão final 1234
Pagamentos feitos pelo cliente -R$ 30,00
03 Dez Pagamento de fatura - R$ 30,00
Lançamentos do cartão físico | EXEMPLO | Final 1234    Total do cartão: R$ 125,00
Total de compras e despesas R$ 125,00
05 Dez LOJA UM R$ 25,00                    18 Dez LOJA EXTERIOR US$ 20,00
                                              Cotação da moeda - R$ 5,00
                                              Conversão para Real - R$ 100,00
"""]


def c6_inactive_page():
    return """Extrato exportado no dia 13 de outubro de 2026 às 15:22
C6 BANK
Agência: 1 Conta: 123456789
Extrato
Período 1 de janeiro de 2026 até 31 de janeiro de 2026
Saldo do dia 13 de outubro de 2026 R$ 100,00
Janeiro 2026 (01/01/2026 31/01/2026)
Entradas: R$ 0,00
Saídas: R$ 0,00
Sem lançamentos no mês
Informações sujeitas a alteração até o final do dia
"""


def unicred_page():
    return """EXTRATO UNICRED
PERIODO DE 01/08/2026 A 31/08/2026 COOP: 000 - AG: 0001 - CONTA 12345-6
SALDO EM 31/07/2026: R$ 100,00
DATA LANCAMENTOS VALOR (R$) SALDO (R$)
03/08/2026 CRED DOM ARRANJO DEBITO ( DOC.: 123 ) R$ 25,00 R$ 125,00
03/08/2026 DEBITO SEGURO ( DOC.: 456 ) -R$ 5,00 R$ 120,00
SALDO NO FINAL DO PERIODO R$ 120,00
SALDO ATUAL R$ 120,00
TOTAL DISPONIVEL R$ 1.120,00
LIMITE DE CHEQUE ESPECIAL R$ 1.000,00
LANCAMENTOS FUTUROS R$ 0,00
CENTRAL DE RELACIONAMENTO 3003 7703 0800 647 2930
"""

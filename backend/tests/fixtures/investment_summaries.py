"""Synthetic structures for investment summaries that contain no cash movements."""

CDB_POSITION_SUMMARY = """
Extrato de movimentação mensal - CDB-DI
Dados da conta:
Nome: CLIENTE EXEMPLO
Agência: 0001 Conta: 12345-6
Dados do extrato:
Período: 01/09/2026 à 30/09/2026
Data Histórico Valor(*) IOF IR Valor creditado
Valor
principal
Remuneração (%) Rentab. no período (%)
Data aplicação Data vencimento N. operação
31/08/2026 SALDO ANTERIOR 1.000,00
30/09/2026 SALDO FINAL 1.010,00
Resumo do período:
Saldo anterior Aplicações Resgates/Recompras Vencimentos
Rendim. acumulado Saldo bruto final Impostos estimados Saldo final
Total 1.000,00 0,00 0,00 0,00 10,00 1.010,00 2,00 1.008,00
Posição em 30/09/2026:
N. operação Data vencimento Data aplicação Valor aplicação Remuneração
Valor em
31/08/2026
Valor em
30/09/2026
Rentab. no período (%)
9000000000001 30/09/2027 01/09/2025 800,00 100,00 1.000,00 1.010,00 1,00
TOTAL 800,00 1.000,00 1.010,00
"""

INVESTMENT_BALANCE_SUMMARY = """
01/10/2026 10:00 Banco Itaú S/A
Nome: CLIENTE EXEMPLO
Agência: 0001 Conta: 12345-6
Período: Setembro / 2026
Valor
Saldo em
31/08/2026
(R$)
Aplicações (R$)
Rendimento
apurado no
mês (R$)
Resgates
antec e
vencimentos (R$)
Saldo em
30/09/2026
(R$)
Rendimento pago no mês (R$)
I.O.F. pago no mês (R$)
I.R. pago no mês (R$)
Principal 1.000,00 0,00 0,00 100,00 900,00 0,00 0,00 0,00
Bruto 1.100,00 0,00 10,00 110,00 1.000,00 10,00 0,00 2,00
Líquido 1.080,00 0,00 8,00 108,00 980,00 8,00 0,00 2,00
Rendimentos apurados no mês, referente às operações resgatadas e ao saldo remanescente.
Dados da conta:
Dados do resumo:
"""

INVESTMENT_SUMMARIES = [CDB_POSITION_SUMMARY, INVESTMENT_BALANCE_SUMMARY]

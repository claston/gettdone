# Banco Inter: cobertura das linhas e saldos por transação no OCR

O caminho `layout_specific_inter_ocr` usa a evidência Textract do layout
`banco_inter_extrato_conta_corrente_saldo_transacao_v1`. As colunas impressas
`Valor` e `Saldo por transação` definem os campos financeiros. Cada valor
de movimento precisa de exatamente um saldo alinhado na mesma página e
de uma descrição correspondente. Valores e sinais vêm das células;
diferenças de saldo não preenchem valores ausentes.

O cabeçalho de cada dia informa o saldo de fechamento daquele dia, antes
das suas movimentações. Esse cabeçalho fornece data e controle financeiro,
sem virar transação ou substituir o saldo de outro movimento. Data e saldo
esperado continuam entre páginas. Rodapés de atendimento ficam fora das
descrições e dos candidatos financeiros. O saldo atual no resumo inicial
pode ser posterior ao período solicitado e não substitui seu fechamento.

São exigidos:

- correspondência única entre descrição, valor e saldo por geometria;
- encadeamento de todos os saldos por transação com aritmética decimal;
- fechamento de cada dia igual ao último saldo desse dia;
- datas crescentes, válidas e dentro do período;
- consumo de todas as células financeiras identificadas, sem ignorar
  valores ilegíveis, duplicados ou manuscritos.

Abertura é calculada a partir do primeiro movimento e seu saldo impresso,
quando o documento não imprime um saldo anterior separado. Isso não
permite inferir ou modificar o valor de movimentos. Erros de associação
ou de controle exigem revisão e não caem silenciosamente no parser
genérico. Os modos Textract `text` e `analysis` usam a mesma validação.

## Caso C08

A conferência das sete páginas encontrou 193 células de movimento, todas
com saldo correspondente, enquanto a conversão anterior preservava 161
linhas. A correção produz:

- 193 movimentos, líquido de −R$ 246,99;
- abertura de R$ 1.358,83 e fechamento de R$ 1.111,84;
- zero inconsistências de saldo, antes cinco;
- proveniência preservada por regiões e referências das linhas.

O reprocessamento utiliza a evidência OCR já salva, sem nova chamada AWS.
O resumo atual de R$ 336,30, consultado após o período, continua separado
do fechamento mensal. Os testes versionados usam apenas dados sintéticos
e validam upload público, worker e downloads CSV/XLSX/OFX.

Não há migração, novo serviço ou mudança de contrato público. Resultados
antigos precisam ser reprocessados para receber os ajustes. Rollback:
reverter o commit e republicar API e worker.

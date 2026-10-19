# Stone: colunas financeiras e recuperação de OCR

O extrato Stone usa as colunas DATA, TIPO, DESCRIÇÃO, VALOR, SALDO e
CONTRAPARTE. O OCR pode devolver o nome da contraparte antes do tipo,
perder os dígitos de uma tarifa ou omitir o primeiro dígito do saldo.

O parser confere a geometria dessas colunas quando há evidência Textract,
tanto no modo `text` quanto em `analysis`. O tipo explícito Entrada/Saída
determina o sinal; saldo e valor são lidos em suas próprias colunas.
Variações monetárias já aceitas, como `RS 0,12`, `R$ 164.00` e
`R$ $39,54`, continuam representando os valores impressos.

## Condições para recuperar um campo

A recuperação usa cinco movimentos consecutivos da mesma data e página,
em ordem decrescente: movimento anterior, tarifa, crédito Pix Maquininha
associado, movimento posterior e outro movimento posterior. Os quatro
movimentos testemunhas precisam ter valores e saldos legíveis. Devem valer
simultaneamente, com aritmética decimal:

- saldo anterior menos valor anterior = saldo do crédito associado;
- saldo posterior menos valor posterior = saldo do último movimento;
- saldo compartilhado menos saldo posterior = crédito associado mais tarifa;
- a tarifa deve ser uma saída e o valor líquido do par deve ser positivo.

Uma tarifa pode ser recuperada apenas se sua célula de valor contém somente
o marcador monetário e seu saldo legível é igual ao do crédito associado.
Um saldo malformado iniciado por `0.xxx,xx` pode ser recuperado apenas se
adicionar exatamente um dígito inicial produz o saldo confirmado e a tarifa
impressa também fecha a conta. Valores impressos legíveis não são substituídos
para forçar a conciliação.

Cada recuperação preserva o OCR original e inclui as regiões e referências
dos movimentos usados na conferência. Os avisos
`ocr_amount_recovered_from_balances` e `ocr_balance_recovered_from_context`
mantêm a recomendação de revisão e seguem os contratos existentes de
preview, persistência e relatórios. A interface pública apresenta a origem
da recuperação; o PRO já marca linhas com avisos como suspeitas.

## Limites e operação

Sem cabeçalhos ou âncoras geométricas confiáveis, mantém-se a leitura textual
existente; uma linha incompleta que poderia consumir saldo como valor é
recusada. Evidência monetária manuscrita, tipos conflitantes, valores
duplicados na mesma célula e testemunhas inconsistentes não autorizam a
recuperação. A ambiguidade financeira não cai silenciosamente no parser
genérico. A recuperação não atravessa páginas ou datas.

Não há migração, alteração de schema, novo serviço ou chamada adicional de
OCR. Resultados antigos permanecem válidos; a correção vale para novos
processamentos. O rollback consiste em reverter o commit e publicar API e
worker anteriores. Os novos avisos são strings opcionais compatíveis com
leitores anteriores.

## Validação dos casos coletados

Replay local com PDFs e evidências OCR já salvos, sem novas chamadas AWS:

| Caso | Correção | Transações | Alertas de consistência antes/depois |
| --- | --- | ---: | ---: |
| C25 | Saldo R$ 128,34 → R$ 10.128,34 | 164 | 3 → 0 |
| C26 | Pix de +R$ 102,00 → −R$ 102,00 | 151 | 2 → 1 |
| C27 | Tarifa de −R$ 5.589,91 → −R$ 0,15; saldo R$ 5.589,91 preservado | 151 | 3 → 1 |

Os 24 outros casos do conjunto mantiveram resultado e assinatura das linhas.
As recuperações C25/C27 têm um aviso próprio de revisão, distinto de erro de
consistência. Os alertas restantes C26/C27 ficam nos limites dos trechos de
extrato e não foram suprimidos. PDFs e evidências de clientes ficam fora do
Git; os testes versionados usam apenas documentos sintéticos.

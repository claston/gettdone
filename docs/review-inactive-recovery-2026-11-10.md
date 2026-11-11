# Ausência de movimentos e orientação de revisão

Este lote trata a recusa confirmada do BB sem movimentação e a divergência entre
a classificação de qualidade e a orientação apresentada ao usuário.

## BB explicitamente sem movimentação

A estrutura identificada contém a declaração `A CONTA NAO FOI MOVIMENTADA`,
conta/agência, mês/ano e dois saldos impressos com indicação C/D. O leitor exige
uma página completa, saldo anterior igual ao final e fechamento no último dia
do período. Valores, sinais ou datas ilegíveis, movimentos adicionais, páginas
extras e imagens na região de lançamentos impedem confirmar ausência de movimentos.
O logotipo no cabeçalho não é tratado como movimento oculto.

A extração nativa e o OCR usam a mesma política de reconhecimento. O resultado
tem zero lançamentos, mantém banco, período e saldos, e conserva a evidência de
origem. O OFX vazio usa o período e a data do saldo final impressos quando esses
metadados estão disponíveis. Não é criada uma transação para representar o saldo.

## Confiança média e qualidade

A orientação consulta a regra de qualidade existente. Apenas confiança média,
com classificação `clean` e sem outros indícios de incerteza, recebe uma mensagem
informativa. Os indicadores técnicos originais permanecem intactos: a mudança não
declara um saldo global reconciliado nem modifica transações.

Saldo inferido, controles ausentes, cobertura parcial, dúvidas de OCR, avisos por
linha e blocos pendentes continuam exigindo revisão. Uma recomendação antiga ou
um aviso desconhecido não é silenciosamente descartado.

API e resultados antigos consultados usam a mesma orientação. Insights
informativos deixam de ser identificados como recomendações de revisão. A
consulta de resultados antigos não reescreve seus dados armazenados.

## Motivos concretos

Quando a evidência existe, a mensagem informa página/linha, saldo calculado,
saldo informado e diferença. Também identifica páginas de blocos pendentes e
campos de data/valor que exigem conferência. Não atribui uma localização inventada
a um alerta global. A interface Free mantém os detalhes junto dos avisos
específicos; o PRO preserva a distinção entre informação e revisão necessária.

## Validação e compatibilidade

- Replay local do original BB: zero movimentos, saldos zero, banco 001 e período
  01/02/2026–28/02/2026 preservados.
- Nas 447 capturas auditadas, as 201 de confiança média e qualidade `clean`
  passam a orientação informativa. As outras 14 `clean`, com saldo inicial
  inferido ou controles ausentes, permanecem em revisão. Isso não comprova que
  todos os 201 usuários tenham sido obrigados a revisar na interface antiga.
- Regressões fictícias cobrem nativo/OCR, sinais, páginas adicionais, valores
  conflitantes, período, respostas antigas, detalhes de origem e HTTP real com
  downloads CSV/Excel/OFX. Browser smoke cobre as três páginas Free em desktop e
  mobile, incluindo conversão e restauração.
- Sem migração, alteração de dependências, reprocessamento de clientes ou deploy.
  O período usa o contrato existente de partes do documento; o argumento interno
  de exportação é opcional e mantém o comportamento anterior quando ausente.
- Rollback: reverter o commit e publicar a versão anterior. Não há rollback de
  banco. Os 34 PDFs inconclusivos permanecem fora deste lote.

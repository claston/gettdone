# Casos remanescentes da recuperação — 04/11/2026

Este lote parte de `4cc1b3f`, a versão encontrada nas capturas da auditoria até
12h07 de Brasília. As PRs #546 e #547 são independentes e ainda não estavam
integradas durante a validação. Nenhuma conversão em produção foi alterada.

## Comportamento corrigido

| Documento | Resultado local | Conferência e limites |
| --- | --- | --- |
| PinPag, movimentos agrupados por dia | 53 movimentos | Datas e valores coincidem com levantamento independente; totais de entradas, saídas e saldos diários conferem. |
| Dois Sicoob/SISBR | 45 e 59 movimentos | Aceita separadores diferentes entre datas explícitas e indicadores C/D minúsculos; controles financeiros conferem. |
| Maree | 115 movimentos | Corrige 19 créditos antes invertidos pelo texto “Pagamento”; prevalece o sinal da coluna monetária. |
| Cresol, duas páginas | 55 movimentos | Exclui quatro lançamentos futuros; mantém revisão quando o OCR perdeu C/D e o histórico fornece a direção. |
| PagBank, vendas | 2.144 registros | Preserva canceladas, pendentes e aprovadas. Treze cancelamentos com taxa e líquido zerados não recebem a equação de venda ativa; 2.156 conferências aplicáveis passam. Não representa liquidação bancária. |
| Ágora, custódia mensal | 25 observações | 13 posições e 12 proventos mensais; 13 conferências passam. Não inventa dia para o crédito mensal. |
| BRB FCBR76 | 33 observações | 22 posições, sete resgates e quatro resumos; 15 conferências passam. Conserva impostos, datas e complemento impresso sem atribuir significado financeiro não confirmado. |

Posições e relatórios de vendas usam o contrato financeiro existente e exportam
CSV/Excel. Não geram lançamentos fictícios de conta corrente ou OFX. Movimentos
legitimamente repetidos e referências às páginas originais são preservados.

## Nova leitura visual e pendências

Com autorização explícita, os três PDFs de BB, Cresol e Sicredi foram enviados
ao Textract para validação local: 17 páginas renderizadas e duas repetições de
página em resolução maior. Os arquivos e respostas permanecem no diretório
local ignorado pelo Git; os testes versionados usam documentos fictícios.

- **Sicredi:** a camada de texto invisível corrompida passa a exigir OCR visual.
  A nova leitura produziu 252 movimentos. O resumo “Saldo da Conta”, atualizado
  depois do período, não entra no extrato. Saldos intermediários ilegíveis
  mantêm a revisão e impedem declarar um saldo global validado.
- **Cresol parcial:** a nova leitura produziu 397 movimentos e excluiu três
  depósitos bloqueados. O arquivo contém páginas 10–15 de 23; não recebe
  validação global. Persistem divergências e sinais recuperados do histórico.
  O OCR antigo continua insuficiente e é bloqueado. A nova leitura em imagens
  foi uma operação de validação, não uma rotina automática de releitura
  adicionada ao produto; recuperar esse original depende da qualidade do OCR.
- **BB:** referência de documento opcional e inclinação das linhas estão
  cobertas, mas o documento real ainda falha por valores ilegíveis, mesmo no
  novo OCR. Não se trunca um terceiro dígito nem se calcula um movimento pela
  diferença de saldos. É necessário obter evidência mais legível.

O aumento de bloqueios com OCR antigo nesses dois casos não deve ser contado
como queda de qualidade de extração: o resultado anterior continha informação
financeira insegura. Tampouco a recuperação local com novo OCR prova que o
mesmo resultado ocorrerá automaticamente em produção.

## Evidência de regressão e reprodução

O replay offline abrangeu 35 PDFs únicos: 21 conversões fora do lote mantiveram
exatamente o multiconjunto de datas e valores, seis falhas passaram a leitura
estruturada, quatro resultados mudaram intencionalmente e quatro falhas
permaneceram. As quatro falhas incluem BB e três casos das PRs anteriores.
Um 36º PDF, com fonte sem mapeamento, pertence à #547 e não entrou no replay.
Os três documentos com nova leitura visual foram avaliados separadamente.
Esses números descrevem a amostra; não estimam a taxa global de sucesso.

Testes de regressão: `backend/tests/test_remaining_recovery_cases.py` e
`backend/tests/test_custody_period_reports.py`. Cobrem formatos nativo/OCR,
datas e valores ilegíveis, contas distintas, movimentos repetidos, proveniência,
paginação parcial, controles diários e distinção entre caixa e observações.

Validação HTTP local com arquivos fictícios e o BRB privado: `POST /convert` e
`GET /report/{analysis_id}` retornaram 200 para CSV, extrato diário e custódia,
além do BRB, com download dos formatos permitidos; relatório inexistente retornou 404.
`POST /analyze`, citado no checklist histórico, está desativado e retornou 405.

O runner `scripts/test_backend.py` terminou com 4.210 testes aprovados,
56 ignorados e dois resultados esperados de falha. Os últimos ajustes de
cancelamento e exportação foram verificados depois com os 50 testes dos quatro
arquivos de regressão envolvidos. Ruff, lint de texto/navegação do frontend e
`git diff --check` passaram.

Não há migração, alteração de armazenamento ou reprocessamento de produção.
O rollback consiste em reverter o commit. Após eventual implantação, acompanhar
recusas por evidência ilegível, encaminhamento ao OCR, cobertura de movimentos
e necessidade de revisão por motivo, além da taxa agregada de falhas.

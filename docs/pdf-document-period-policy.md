# Período documental e conversão de movimentos

A ausência do intervalo do cabeçalho deixa de ser, por si só, motivo para
recusar movimentos legíveis. A política é compartilhada pelos leitores de
extratos e relatórios com movimentos, tanto em PDF nativo quanto em OCR.

## Comportamento

- Com datas completas, valores, sinais e associação de colunas legíveis, os
  movimentos continuam disponíveis para prévia, edição e exportação.
- Sem período identificado, o resultado recebe `document_period_unavailable`
  e `review_recommended`. Esse motivo aparece no conversor gratuito, no PRO
  e nos motivos de atenção do painel Admin. Ele não é classificado como
  divergência financeira apenas pela ausência do cabeçalho.
- O período continua necessário quando é a única fonte do ano/mês para datas
  abreviadas ou para separar blocos de extratos com segurança. Nesses casos,
  faltam dados para interpretar os movimentos sem adivinhar.
- Períodos conflitantes dentro do mesmo bloco ou inválidos, contas misturadas, valores ilegíveis,
  sinais ambíguos e colunas sem associação conservam suas proteções. Relatórios
  de posições sem movimentos precisam manter uma data de posição inequívoca.
- Divergências financeiras legíveis conservam os avisos da política de revisão
  existente; os valores não são ajustados para forçar fechamento.

O intervalo observado entre lançamentos não certifica cobertura do documento.
Relatórios financeiros sem cabeçalho deixam `period_start`/`period_end` sem
valor. No histórico, as datas dos movimentos podem continuar compondo o quadro
por mês, identificadas por `basis=movement_dates`, conforme o contrato atual.
Não são convertidas em cobertura documental confirmada.

## Documentos com vários meses

A visão de extração financeira está registrada no `AGENTS.md`: o objetivo é
extrair os movimentos financeiros legíveis e tentar reconciliá-los, sem exigir
que o arquivo corresponda a um único mês. O leitor compartilhado identifica
blocos por período impresso ou por saldos de abertura com datas independentes,
processa cada bloco e reúne as transações, preservando as páginas originais.
Saldos de abertura/fechamento são controles, não novas transações.

A numeração de páginas pode recomeçar em cada extrato. Cada bloco é conferido
separadamente; o reinício não indica, por si só, páginas ausentes. Lacunas de
período, ordem invertida ou saldos entre blocos que não conferem geram
`statement_blocks_not_contiguous` e revisão, mantendo os movimentos e os saldos
individuais em `document_parts`. Nesses casos, não se publica saldo global
confirmado. A continuidade documental e financeira precisa conferir para
informar saldos globais validados.

O processamento mantém os modelos suportados e sua semântica financeira.
Um erro real de leitura num bloco posterior não pode ser ocultado por um
resultado aparentemente completo contendo apenas os primeiros meses.

## Compatibilidade e validação

Não há alteração de tabelas, migração, backfill ou reclassificação retroativa.
O indicador `document_period_unavailable` é opcional e tem valor padrão zero
na resposta. O motivo de revisão usa as estruturas de métricas e avisos já
existentes. CSV, XLSX e OFX conservam seus fluxos e formatos suportados.

Os testes retiram o período de PDFs fictícios e comparam datas, sinais, valores
e multiplicidade com os documentos completos. Cobrem leitores bancários,
vendas, investimentos, OCR, moeda estrangeira, contexto de datas abreviadas,
avisos isolados entre requisições e operações HTTP reais de conversão,
consulta e exportação. O comparativo de compatibilidade usa `origin/main`.

Implantar backend/worker e frontends juntos para disponibilizar a regra e seu
aviso. Para rollback, restaurar as versões anteriores desses componentes;
não há rollback de esquema. Conversões e motivos já registrados permanecem
no histórico. Um novo envio é necessário para reprocessar falhas anteriores.

# Relatórios genéricos de contas a pagar e a receber

O mesmo leitor atende `accounts_payable_report` e `accounts_receivable_report`.
A identidade vem do título do documento; os campos são associados por aliases
dos cabeçalhos e coordenadas, sem cadastrar um modelo por fornecedor ou ERP.
Colunas podem aparecer em outra ordem. O vocabulário fica centralizado em
`title_report_detection.py` e a interpretação é compartilhada por PDF nativo
e pelo OCR já disponível no produto.

O contrato reutiliza `financial_events_v1`. Cada linha vira `payable_title`
ou `receivable_title`, com referência, contraparte, descrição, datas por papel,
valor original, saldo em aberto, valor pago/recebido e ajustes quando informados.
Situação e campos originais são preservados. Colunas adicionais, como projeto,
plano de contas e centro de custo, permanecem nos campos de origem. Duplicatas
impressas são mantidas e cada título aponta para a página e linha de origem.

Vencimento não é data de pagamento. Uma situação “pago” ou “recebido” neste
relatório não confirma lançamento em conta bancária. Todos esses eventos têm
`cash_effect=null`, não entram nos totais de entradas/saídas e não participam
da conciliação bancária. Futuras conciliações podem relacionar títulos e
movimentos, preservando ambos os registros e sua proveniência.

## Conversão e exportação

- Excel: abas de eventos, campos originais, detalhes, validações e documento.
- CSV: campos normalizados e colunas `source.<cabeçalho>` com os dados originais.
- OFX: indisponível, inclusive quando o usuário informa banco/agência/conta.
- A prévia usa vencimento, emissão ou movimento conforme o papel da data
  identificada. O Pro mostra contraparte, situação e saldo em aberto.

O leitor exige cabeçalhos identificáveis, um valor original e uma data
legível por linha. Coluna de pagamento vazia é permitida. Valores e datas
necessários ilegíveis/ambíguos produzem erro específico; não há fallback
para inventar movimentações bancárias. Diferenças de totais e situações
inconsistentes geram validações e revisão, preservando os valores extraídos.
Um relatório vazio exige total zero explícito.

As regras são determinísticas: não há invocação de Bedrock nem novo mecanismo
de OCR. PDF digitalizado continua dependendo do OCR configurado no ambiente.
A ausência de estrutura suficiente é informada ao usuário; não há promessa
de interpretar qualquer tabela arbitrária ou documento ilegível.

## Compatibilidade e validação

O fluxo usa o armazenamento, os contratos de API, o exportador e os workers
existentes. A nova categoria a receber é adicionada à lista de tipos aceitos
nas métricas, sem migração de banco. Snapshots de conversões anteriores não
são reescritos. A classificação passa à versão `2026-10-23.3`.

Os testes usam relatórios fictícios com colunas em ordens diferentes, aliases,
campos extras, PDF rotacionado, várias páginas, títulos repetidos, dados
ilegíveis, situações parciais e totais divergentes. Os testes HTTP percorrem
upload, worker, relatórios, downloads e recusa de OFX. O relatório privado
que motivou o ajuste foi reanalisado com OCR salvo: 124 títulos extraídos.
Nenhum PDF ou dado privado é incluído no Git.

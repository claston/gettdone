# Documento do lançamento

`document_number` é uma referência textual opcional, separada da descrição. A
origem pode ser a coluna Documento do PDF, uma coluna identificada no CSV/Excel,
ou `REFNUM`/`CHECKNUM` no OFX. Preservamos zeros à esquerda e referências repetidas.
Não extraímos números arbitrários da descrição nem removemos lançamentos por
compartilharem essa referência. Quando a fonte não permite separar o documento,
o campo permanece ausente/nulo; não adivinhamos seu valor.

No Excel, máscaras inteiras de preenchimento com zeros, como `000000`, são
respeitadas exclusivamente na coluna Documento: `1103` com essa máscara vira
`001103`. Formatos monetários e as células de valor não são reinterpretados.
No CSV, o nome canônico da coluna tem precedência (`amount`, por exemplo);
sem ele, aliases concorrentes geram erro explícito de ambiguidade. A inclusão
de Documento não amplia o vocabulário dos campos financeiros.

A prévia Free e a revisão PRO mostram a coluna quando algum lançamento tem
documento. Ela é informativa: editar histórico, data ou valor preserva a referência
da origem. CSV, Excel de conversão e relatório Excel recebem uma coluna adicional
ao final; no Excel seus valores são células de texto, inclusive se começam por
`=`. Exportações sem referência mantêm suas colunas anteriores.

## OFX

A [especificação OFX, seção 11.4.4.1](https://financialdataexchange.org/common/Uploaded%20files/OFX%20files/OFX%20Banking%20Specification%20v2.3.pdf)
distingue `CHECKNUM` (cheque ou outra referência), `REFNUM` (referência única) e
`FITID` (identificação da transação para importação).

- Referência genérica do extrato: `CHECKNUM`, até 12 caracteres.
- Referência explicitamente importada como `REFNUM`: preserva `REFNUM`, até 32
  caracteres. `document_reference_type` mantém essa semântica no armazenamento.
- Se o OFX de origem contém ambos, `REFNUM` tem precedência no campo Documento.
- Referência que excede o limite: preservação integral no `MEMO`, com o rótulo
  `Documento:`, sem truncar nem presumir unicidade. Reimportar esse texto não
  promove automaticamente o número para Documento.
- O algoritmo de `FITID` permanece inalterado. Adicionar Documento não muda o
  FITID, e documentos iguais em movimentos legítimos não causam deduplicação.

## Compatibilidade e validação

São campos JSON opcionais; dados antigos continuam legíveis. O armazenamento
canônico já preserva atributos adicionais em JSON, portanto não há migration,
backfill ou alteração de tabelas. Backend/worker atualizados devem preceder a
interface, que também funciona sem os campos. Arquivos já convertidos não são
reescritos; é necessário reconverter a origem para recuperar referências ausentes.

Rollback: reverter a PR e publicar as versões anteriores. Valores e saldos não
mudam; a versão antiga pode omitir a nova referência ao regravar/exportar uma
análise. Preserve os arquivos JSON/exportações existentes se precisar conservar
esse novo metadado durante um rollback.

Os testes cobrem PDF/CSV/Excel/OFX, armazenamento, edição por HTTP real,
exportações, zeros à esquerda, repetições e FITID. A evidência sintética está em
[transaction-document-review.json](transaction-document-review.json). Os diffs
exatos autorizados pelo escopo solicitado ficam em
[transaction-document-approvals.json](transaction-document-approvals.json),
vinculados à revisão base e à árvore da aplicação. A revisão normal da PR segue
pendente; os números das fixtures foram auditados durante a implementação.

A caracterização antiga é preservada; a nova versão
`backend/tests/fixtures/free_converter_document_baseline.json` registra o contrato
com Documento. Nenhum campo financeiro, FITID ou proveniência foi mascarado no
comparador. Agrupar diferenças sob a mesma autorização só evita repetir metadados:
cada diferença ainda exige correspondência exata de caso, caminho, antes/depois,
base e árvore. Os oráculos financeiros independentes permanecem inalterados.

Após integrar a PR #554 da `main`, os vínculos foram revalidados contra a base
`37fb572` e a árvore da aplicação `8fb923c6b67ee71a58892b059aaca6f8d343ead7`.
As 3.304 diferenças de PDF e 3.642 de revisão são exatamente as já enumeradas;
a atualização altera apenas seus vínculos de revisão. O registro dessa
conferência está em `merge_revalidation` na evidência sintética. Uma integração
futura que altere a base ou a árvore exige nova conferência das diferenças antes
de atualizar esses vínculos.

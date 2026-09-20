# PDF canônico de layout v3

O v3 é um artefato privado para diagnóstico de conversões. Ele preserva os sinais transacionais necessários para reproduzir problemas de parser, sem manter dados que identifiquem correntistas ou outras partes da transação.

## Conteúdo preservado

- tamanho e paginação do documento, linhas e posições dos elementos;
- nome público do banco validado pelo catálogo;
- cabeçalhos e estrutura da tabela;
- datas e horários;
- históricos operacionais, como `PIX RECEBIDO`, `DEB PIX CHAVE`, `TARIFA` e `RECEBIMENTO TED`;
- valores, sinais, indicadores `C`/`D` e saldos.

## Conteúdo mascarado

- nome ou razão social do correntista;
- nomes e documentos de contrapartes;
- CPF, CNPJ, agência e conta;
- endereço, e-mail e telefone;
- número da coluna `Documento`, chaves, end-to-end IDs e outros identificadores longos.

Os marcadores usados no PDF e no `layout.json` incluem `[TITULAR]`, `[PARTE]`, `[CPF]`, `[CNPJ]`, `[AGENCIA]`, `[CONTA]`, `[ENDERECO]` e `[IDENTIFICADOR]`.

## Ativação e compatibilidade

Ative:

```env
CANONICAL_LAYOUT_CAPTURE_ENABLED=true
CANONICAL_LAYOUT_V3_ENABLED=true
```

`CANONICAL_LAYOUT_V3_ENABLED` tem precedência quando a flag do v2 também estiver ativa. Os artefatos são gravados em `conversion/canonical-layouts/candidates/v3`, sem alterar ou migrar os objetos v1/v2 existentes. Para rollback, desative a flag v3; a seleção volta ao v2, quando habilitado, ou ao v1.

Com Textract ativo, a v3 reutiliza as coordenadas já extraídas. Sem coordenadas, mantém posições estimadas. Diferentemente da v2, que limita a amostra a cinco páginas, a v3 processa até `CANONICAL_LAYOUT_CAPTURE_MAX_PAGES` e registra `source_page_count` e `sampled_page_count` no manifesto.

Antes do armazenamento, a validação relê o PDF e o manifesto, procura padrões estruturados de PII e compara os fragmentos removidos com o conteúdo final. PDFs com conteúdo ativo, anotações, imagens ou XObjects continuam sendo rejeitados.

# Avaliacao do motor de normalizacao e modularizacao

Data: 2026-05-10

## Contexto

Esta avaliacao compara o motor atual com o plano local `local/plano_evolucao_motor_ofx_simples.docx`.

Objetivo do produto: converter PDF de extratos financeiros de bancos brasileiros para OFX/Excel com alta confiabilidade, rastreabilidade tecnica e privacidade por padrao.

## Diagnostico rapido

O motor atual ja tem uma base funcional: parseia CSV, XLSX, OFX e PDFs com texto extraivel; detecta alguns perfis de PDF brasileiros; normaliza data, descricao, valor e tipo; gera OFX/CSV/XLSX temporarios; e expoe metricas de processamento para PDF.

Ainda nao considero o motor pronto para prometer "grande maioria dos PDFs de extratos bancarios brasileiros". Hoje ele e um parser heuristico com boa cobertura inicial para layouts parecidos com os exemplos testados, mas falta uma camada canonica rica, validacao por saldo, fingerprints, FITID deterministico completo por contexto bancario, dataset de regressao real e modularizacao de pipeline.

## Evidencias observadas

- `backend/app/application/pdf_parser.py` concentra extracao, flattening de linhas, selecao de parser, parsing grouped/inline/tabular/columnar, inferencia de sinal, datas e filtros. OCR fica isolado e desativado nesta fase.
- `backend/app/application/normalizer.py` normaliza campos finais, mas o modelo `NormalizedTransaction` so carrega `date`, `description`, `amount` e `type`.
- `backend/app/application/ofx_writer.py` gerava `FITID` sequencial, o que criava risco de duplicidade quando o mesmo PDF fosse convertido novamente com ordenacao diferente ou linhas inseridas.
- `backend/app/application/pdf_layout_inference.py` tinha perfis para Nubank, Itau, Santander, Bradesco, Banco do Brasil, Caixa, Inter e Sicredi, mas dependia de termos de layout especificos.
- O PDF local `local/Nubank_2026-04-15.pdf` foi processado com sucesso e retornou 2 transacoes, mas antes deste ajuste caiu em `generic_statement_ptbr` mesmo contendo sinal de Nubank.
- Os artefatos temporarios em `TempAnalysisStorage` persistem `analysis.json`, `converted.ofx`, `converted.csv`, `converted.xlsx` e `report.xlsx` ate o TTL; isso e util para fluxo atual, mas precisa de politica explicita de dados sensiveis antes de evoluir historico/auditoria.

## Lacunas de confiabilidade

### P0

- Nao existe `CanonicalTransaction` rico com origem auditavel: pagina, linha, metodo de extracao, saldo apos lancamento, banco, layout, conta/agencia mascaradas, confianca e warnings.
- Nao ha validacao por saldo inicial/final ou saldo corrente linha a linha.
- Nao ha `fileBinaryHash`, `fileCanonicalHash`, `conversionId` tecnico e versao formal de motor/parser.
- Nao ha golden dataset versionado com PDFs/expected JSON para bancos brasileiros.
- O parser de PDF mistura muitas responsabilidades em um unico arquivo, dificultando evoluir bancos/layouts sem regressao.

### P1

- A deteccao de layout ainda e baseada em tokens estaticos e nao possui explicabilidade suficiente por perfil.
- O OCR esta desativado nesta fase; ainda nao ha contrato de qualidade para PDF escaneado.
- A normalizacao semantica de descricoes e simples; nao separa contraparte, canal, tipo de operacao, documento e ruido.
- A geracao de Excel ainda e mais uma tabela de conversao do que um relatorio tecnico/auditavel.

### P2

- Nao ha score por transacao.
- Nao ha relatorio de inconsistencias especifico para conversao PDF -> OFX.
- Nao ha estrategia clara de privacidade para metadados persistidos em historico pago/profissional.

## Modularizacao recomendada

Estrutura alvo incremental:

```text
backend/app/application/
  normalization/
    text.py
    amount.py
    date.py
    transaction.py
  ingestion/
    detector.py
    pdf/
      extractor.py
      layout_profiles.py
      parser_grouped.py
      parser_inline.py
      parser_tabular.py
      parser_columnar.py
  conversion/
    engine.py
    audit.py
    fingerprints.py
    balance_validation.py
  export/
    ofx_writer.py
    excel_writer.py
```

Ordem segura:

1. Extrair primitivos compartilhados de texto, data e valor sem mudar comportamento.
2. Criar `CanonicalTransaction` em paralelo ao `NormalizedTransaction`, sem remover o modelo legado.
3. Fazer o PDF parser retornar transacoes canonicas com origem e warnings.
4. Introduzir fingerprints e FITID deterministico usando HMAC.
5. Implementar validacao por saldo como etapa separada e opcional no inicio.
6. Criar golden dataset pequeno com expected JSON anonimizados/sinteticos.
7. Migrar exportadores OFX/Excel para consumir o contrato canonico.

## Primeiros ajustes aplicados nesta branch

- Criado `backend/app/application/normalization/text.py` para centralizar normalizacao textual compartilhada.
- `normalizer.py`, `pdf_parser.py` e `pdf_layout_inference.py` passaram a usar esse modulo comum.
- A inferencia Nubank passou a reconhecer explicitamente o token `NUBANK`, reduzindo fallback generico em PDFs Nubank com texto extraivel.
- O OFX passou a gerar `FITID` deterministico via HMAC em `backend/app/application/ofx_identity.py`, com ocorrencia por transacao duplicada no mesmo dia/valor/descricao.

## Recomendacao executiva

Nao recomendo vender a cobertura atual como "grande maioria dos PDFs de bancos brasileiros" ainda. Recomendo posicionar como "PDFs com texto extraivel e layouts suportados em evolucao" ate termos:

- dataset minimo com Nubank, Itau, Santander, Bradesco, Banco do Brasil, Caixa, Inter e Sicredi;
- recall/precision por banco;
- validacao de saldo;
- FITID estavel;
- auditoria tecnica sem conteudo financeiro em texto claro;
- relatorio de baixa confianca/inconsistencias.

Com os primeiros ajustes desta branch, o projeto comeca a sair de arquivos soltos para modulos de dominio, mas ainda precisamos fazer a modularizacao grande em etapas pequenas e testadas.

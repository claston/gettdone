# Continuidade do motor de normalizacao

Data: 2026-05-11

## Objetivo

Evoluir o motor de conversao/normalizacao de extratos financeiros sem publicar direto em producao.

## Estrategia de branches

- `main`: continua protegida para producao.
- `integration-normalization-engine`: branch intermediaria de integracao e testes.
- `audit-normalization-modularization`: branch da PR-109, ja incorporada em `integration-normalization-engine`.
- `feat/layout-profile-registry`: primeira branch de continuidade, baseada em `integration-normalization-engine`.

Fluxo recomendado:

```text
main
  |
  +-- integration-normalization-engine
        |
        +-- PR-109 incorporada: audit-normalization-modularization
        |
        +-- feat/layout-profile-registry
        +-- proximas PRs pequenas do motor
```

As proximas PRs devem usar `integration-normalization-engine` como base, nao `main`.

## O que esta PR adiciona

- Move os meta-modelos locais para uma pasta versionada do backend:
  - `backend/app/application/layout_profiles/profiles/`
- Cria um registry leve para ler a secao `layout_profile` dos YAMLs:
  - `backend/app/application/layout_profiles/registry.py`
- Conecta os perfis declarativos na inferencia de layout PDF:
  - `backend/app/application/pdf_layout_inference.py`
- Aplica `classifier.min_score_hint` para evitar que um perfil declarativo vença com sinais parciais demais.
- Ajusta o classificador para tratar perfis declarativos nao-genericos como evidencia de extrato bancario:
  - `backend/app/application/document_classifier.py`

Nesta etapa, os modelos entram no motor como classificadores declarativos de layout. A PR ainda nao aplica todas as regras de parsing, sinal, saldo e revisao manual descritas nos YAMLs.

## Decisoes tecnicas

- Nao foi adicionada dependencia nova de YAML.
- O loader le apenas o subconjunto necessario da secao `layout_profile`:
  - `profile_name`
  - `bank`
  - `confidence`
  - `classifier.required_keywords`
  - `classifier.optional_keywords`
  - `classifier.negative_keywords`
  - `classifier.min_score_hint`
  - `table_detection.header_keywords`
- Os YAMLs completos foram preservados para alimentar as proximas etapas.
- A inferencia usa `min_score_hint` como corte por perfil antes de comparar layouts declarativos contra os perfis legados e o fallback generico.

## Proximas PRs recomendadas

1. Criar `CanonicalTransaction` paralelo ao `NormalizedTransaction`, com:
   - banco/layout
   - pagina/linha
   - saldo
   - documento/id
   - warnings
   - confianca
2. Implementar validacao por saldo a partir de `balance_consistency_check`.
3. Transformar `examples_from_image.sample_rows` em fixtures golden sinteticas.

## Fatia seguinte: colunas tabulares declarativas

Branch: `feat/layout-profile-table-columns`

- O registry passa a carregar:
  - `table_detection.expected_column_order`
  - `table_detection.column_aliases`
- O parser tabular de PDF usa o perfil inferido quando encontra cabecalho compativel por aliases.
- Para tabelas com colunas separadas `credit`/`debit`/`balance`, o valor da transacao passa a ser escolhido pela ordem declarativa, evitando confundir saldo ou coluna zerada com valor movimentado.
- A heuristica legada continua sendo usada quando nao ha perfil declarativo aplicavel.

## Validacao executada

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_layout_profile_registry.py backend\tests\test_pdf_layout_inference.py backend\tests\test_document_classifier.py -q
```

Resultado: `21 passed`.

Observacao: como a execucao ocorreu dentro de worktree separada e usando o venv do workspace principal, o pytest emitiu avisos de cache sem impacto nos testes.

Validacao complementar apos aplicar `min_score_hint`:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_layout_profile_registry.py backend\tests\test_pdf_layout_inference.py backend\tests\test_document_classifier.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\layout_profiles\registry.py backend\app\application\document_classifier.py backend\app\application\pdf_layout_inference.py backend\tests\test_layout_profile_registry.py backend\tests\test_pdf_layout_inference.py
```

Validacao da fatia de colunas tabulares:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_layout_profile_registry.py backend\tests\test_pdf_layout_inference.py backend\tests\test_document_classifier.py backend\tests\test_pdf_parser.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\layout_profiles\registry.py backend\app\application\pdf_parser.py backend\app\application\pdf_layout_inference.py backend\tests\test_layout_profile_registry.py backend\tests\test_pdf_parser.py backend\tests\test_pdf_layout_inference.py
```

Resultado: `27 passed`; `All checks passed!`.

## Fatia seguinte: normalizacao de valores

Branch: `feat/normalization-amount-module`

- Criado `backend/app/application/normalization/amount.py`.
- `parse_amount` centraliza o parsing monetario antes espalhado em `csv_parser.py`.
- CSV mantem `_parse_amount` como wrapper de compatibilidade para reduzir o impacto da mudanca.
- OFX, XLSX, planilhas operacionais e PDF passam a usar o modulo compartilhado diretamente.
- O parser passa a aceitar formatos com sinal e moeda em posicoes variadas:
  - `-R$ 10,00`
  - `R$ -10,00`
  - `10,00-`
  - `10.00-`
  - sinal unicode de menos
- `apply_amount_role_sign` centraliza a regra de sinal para colunas `credit` e `debit`.

Validacao da fatia de normalizacao de valores:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_amount_normalization.py backend\tests\test_csv_parser.py backend\tests\test_xlsx_parser.py backend\tests\test_sheet_parser.py backend\tests\test_ofx_parser.py backend\tests\test_pdf_parser.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_analyze_service_csv.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\normalization\amount.py backend\app\application\csv_parser.py backend\app\application\xlsx_parser.py backend\app\application\sheet_parser.py backend\app\application\ofx_parser.py backend\app\application\pdf_parser.py backend\tests\test_amount_normalization.py
```

Resultados:

- `32 passed`
- `9 passed`
- `All checks passed!`

## Fatia seguinte: CanonicalTransaction em paralelo

Branch: `feat/canonical-transaction-parallel`

- Criado `CanonicalTransaction` em paralelo ao `NormalizedTransaction`:
  - banco/layout: `bank_name`, `layout_name`
  - pagina/linha: `source_page`, `source_line`
  - saldo: `running_balance`
  - documento/id: `document_id`, `external_reference_id`
  - warnings: `warnings`
  - confianca: `confidence`
- Criado adaptador `from_normalized_transaction(...)` em:
  - `backend/app/application/normalization/canonical.py`
- Nesta fatia, o contrato atual dos parsers e servicos foi mantido para evitar regressao:
  - o pipeline continua retornando `NormalizedTransaction`
  - `CanonicalTransaction` entra como modelo paralelo pronto para adocao incremental

Validacao da fatia CanonicalTransaction:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_canonical_transaction.py backend\tests\test_normalizer.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\models.py backend\app\application\normalization\canonical.py backend\tests\test_canonical_transaction.py
```

Resultados:

- `7 passed`
- `All checks passed!`

## Fatia seguinte: CanonicalTransaction no parser PDF (paralelo)

Branch: `feat/pdf-canonical-transaction-metadata`

- `PdfParseResult` passa a expor `canonical_transactions` em paralelo a `transactions`.
- O parser PDF preenche os canônicos via `from_normalized_transaction(...)` sem alterar o contrato legado.
- Metadados preenchidos nesta fatia:
  - `layout_name`
  - `bank_name` (quando perfil declarativo existe)
  - `confidence`
  - `warnings=["layout_fallback"]` quando a inferência usar fallback
- Compatibilidade preservada:
  - `transactions` (NormalizedTransaction) continua sendo o caminho principal atual
  - `canonical_transactions` é opcional no dataclass para manter fixtures antigas de teste

Validacao da fatia CanonicalTransaction no PDF:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\pdf_parser.py backend\tests\test_pdf_parser.py --fix
```

Resultados:

- `8 passed`
- `Found 1 error (1 fixed, 0 remaining).`

## Fatia seguinte: pacote maior de rastreabilidade canônica no PDF

Branch: `feat/pdf-canonical-traceability-pack1`

- Refactor único no parser PDF para carregar contexto de origem por linha:
  - nova estrutura interna com `page_number` e `line_number`
  - parsers `grouped`/`inline`/`tabular`/`columnar` propagam essa origem por transação
- `canonical_transactions` passa a refletir origem real por transação:
  - `source_page`
  - `source_line`
  - mantendo `layout_name`, `bank_name`, `confidence` e warning de fallback
- Compatibilidade preservada:
  - `transactions` legado continua inalterado para o fluxo principal
  - `canonical_transactions` permanece opcional em `PdfParseResult` para manter fixtures antigas

Validacao da fatia de rastreabilidade canônica:

```powershell
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\pdf_parser.py backend\tests\test_pdf_parser.py
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
```

Resultados:

- `All checks passed!`
- `8 passed`

## Fatia seguinte: pacote maior de metadados canônicos tabulares

Branch: `feat/pdf-canonical-balance-document-pack1`

- Extende metadados canônicos no caminho tabular declarativo de PDF:
  - preenche `running_balance` quando coluna de saldo estiver disponível
  - preenche `external_reference_id` via heurística de coluna `document` declarada no profile
- Mantém compatibilidade com o fluxo legado:
  - `transactions` e `description` permanecem sem alteração funcional
  - enriquecimento ocorre apenas em `canonical_transactions`
- Mantém metadados já existentes do canônico:
  - `layout_name`, `bank_name`, `source_page`, `source_line`, `confidence`, `warnings`

Validação da fatia de metadados tabulares:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\pdf_parser.py backend\tests\test_pdf_parser.py
```

Resultados:

- `8 passed`
- `All checks passed!`

## Fatia seguinte: pacote maior de consistência de saldo canônica

Branch: `feat/pdf-canonical-balance-consistency-pack1`

- Adiciona checagem não-bloqueante de consistência de saldo no fluxo canônico PDF:
  - compara `running_balance` sequencial com `previous_balance + current.amount`
  - aplica tolerância de arredondamento
- Em caso de divergência, adiciona warning estruturado em `canonical_transactions`:
  - `balance_consistency_failed`
- Expõe métricas no `parse_metrics` para observabilidade:
  - `balance_consistency_checked`
  - `balance_consistency_failed`
- Compatibilidade preservada:
  - não altera o contrato legado de `transactions`
  - não transforma inconsistência de saldo em erro de parsing nesta etapa

Validação da fatia de consistência de saldo:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\pdf_parser.py backend\tests\test_pdf_parser.py
```

Resultados:

- `9 passed`
- `All checks passed!`

## Fatia seguinte: pacote maior de métricas de qualidade canônica

Branch: `feat/pdf-canonical-quality-metrics-pack1`

- Consolida métricas de qualidade canônica no `parse_metrics` do parser PDF:
  - `canonical_transactions_count`
  - `canonical_with_running_balance_count`
  - `canonical_with_external_reference_count`
  - `canonical_warning_count`
  - `canonical_balance_warning_count`
- Mantém métricas anteriores de consistência de saldo:
  - `balance_consistency_checked`
  - `balance_consistency_failed`
- Objetivo: melhorar observabilidade para rollout incremental sem alterar contrato legado.

Validação da fatia de métricas canônicas:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\pdf_parser.py backend\tests\test_pdf_parser.py
```

Resultados:

- `9 passed`
- `All checks passed!`

## Fatia seguinte: pacote maior de propagação no AnalyzeService

Branch: `feat/analyze-service-canonical-metrics-pack1`

- Faz a propagação explícita das métricas canônicas de PDF em `AnalyzeService`:
  - `balance_consistency_checked`
  - `balance_consistency_failed`
  - `canonical_transactions_count`
  - `canonical_with_running_balance_count`
  - `canonical_with_external_reference_count`
  - `canonical_warning_count`
  - `canonical_balance_warning_count`
- Atualiza o schema `PdfProcessingMetrics` para suportar os novos campos de forma tipada.
- Adiciona/atualiza testes para validar que as métricas chegam no `AnalyzeResponse.pdf_processing_metrics`.

Validação da fatia de propagação:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_analyze_service_multiformat.py backend\tests\test_pdf_parser.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\analyze_service.py backend\app\schemas.py backend\tests\test_analyze_service_multiformat.py
```

Resultados:

- `9 passed`
- `All checks passed!`

## Fatia seguinte: pacote maior de resumo de warnings canônicos

Branch: `feat/pdf-canonical-warning-summary-pack1`

- Adiciona resumo estruturado de warnings canônicos no `parse_metrics` do PDF parser:
  - `canonical_warning_transactions_count`
  - `canonical_warning_types_count`
  - `canonical_warning_types` (lista em string separada por vírgula)
- Propaga os novos campos no `AnalyzeService` para `pdf_processing_metrics`.
- Atualiza `PdfProcessingMetrics` no schema com os novos campos tipados.
- Inclui cobertura de testes para cenários sem warning e com `balance_consistency_failed`.

Validação da fatia de resumo de warnings:

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\app\application\pdf_parser.py backend\app\application\analyze_service.py backend\app\schemas.py backend\tests\test_pdf_parser.py backend\tests\test_analyze_service_multiformat.py
```

Resultados:

- `9 passed`
- `All checks passed!`

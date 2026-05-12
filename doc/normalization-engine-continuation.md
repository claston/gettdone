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

1. Extrair normalizacao de valores para `normalization/amount.py`, incluindo:
   - `-R$`
   - sinal no fim
   - colunas separadas credito/debito
   - sufixos `C`/`D`
2. Criar `CanonicalTransaction` paralelo ao `NormalizedTransaction`, com:
   - banco/layout
   - pagina/linha
   - saldo
   - documento/id
   - warnings
   - confianca
3. Implementar validacao por saldo a partir de `balance_consistency_check`.
4. Transformar `examples_from_image.sample_rows` em fixtures golden sinteticas.

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

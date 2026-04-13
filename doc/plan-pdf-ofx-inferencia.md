# Plano de Implementacao - PDF para OFX sem selecao de banco

## Objetivo

Permitir upload de extrato bancario em `PDF` no backend, extrair transacoes sem exigir escolha manual de banco/layout e entregar:

- preview normalizado no fluxo atual (`POST /analyze`)
- uso no fluxo de conciliacao (`POST /reconcile`)
- arquivo `OFX` bonus para download ao final

Arquivo real de referencia inicial:
`backend/samples/NU_150702837_01NOV2023_30NOV2023.pdf`

---

## Escopo

### Entra no escopo

- suporte a `PDF` no parser de extrato bancario
- inferencia automatica de layout (sem combobox de banco)
- geracao de OFX a partir das transacoes extraidas
- endpoint de download do OFX bonus por `analysis_id`
- testes unitarios e de integracao do fluxo PDF

### Fora do escopo (primeiro corte)

- OCR avancado para PDF escaneado com baixa qualidade
- suporte a todos os bancos de forma exaustiva no primeiro release
- interface de configuracao manual de layout

---

## Arquitetura proposta

1. Entrada de arquivo
- expandir extensoes aceitas para `csv`, `xlsx`, `ofx`, `pdf` em:
  - `AnalyzeService`
  - `parse_bank_statement_rows` (reconcile)

2. Parser PDF dedicado
- novo modulo sugerido: `backend/app/application/pdf_parser.py`
- responsabilidade:
  - extrair texto bruto por pagina
  - localizar linhas de transacao
  - converter para `NormalizedTransaction`

3. Inferencia de layout sem banco manual
- novo modulo sugerido: `backend/app/application/pdf_layout_inference.py`
- estrategia:
  - perfis de layout (regras por padrao textual)
  - score por perfil (anchors de cabecalho, regex de linha, consistencia de colunas)
  - escolha do maior score acima de limiar
  - fallback generico quando nenhum perfil atingir score minimo

4. Geracao OFX
- novo modulo sugerido: `backend/app/application/ofx_generator.py`
- gera `STMTTRN` a partir de `NormalizedTransaction`
- saida salva no diretorio temporario da analise para download com TTL

5. Download do bonus OFX
- rota sugerida: `GET /report/{analysis_id}/bonus-ofx`
- responsavel por buscar arquivo OFX da analise e retornar `404` quando expirado/inexistente

6. Persistencia temporaria
- estender `TempAnalysisStorage` para salvar:
  - `bonus.ofx`
  - metadados de inferencia (layout, score, fallback usado)

---

## Fases de implementacao

## Fase 0 - Spike com dataset real (1 dia)

Objetivo: provar viabilidade tecnica no PDF real antes de fechar design.

Entregas:
- extracao de texto do PDF real
- primeira deteccao de linhas de transacao
- checklist de riscos (quebra de linha, valores com sinal, saldo corrido)

Aceite:
- parser consegue gerar conjunto minimo valido de transacoes para o arquivo real

## Fase 1 - Parser PDF MVP (2 dias)

Objetivo: disponibilizar `PDF` no fluxo `/analyze` com parser funcional.

Entregas:
- `pdf_parser.py` com leitura de linhas e parse de `date/description/amount/type`
- integracao no `AnalyzeService` e no parser de extrato bancario compartilhado
- erros padronizados para PDF invalido/ilegivel

Aceite:
- `POST /analyze` aceita PDF e retorna preview coerente

## Fase 2 - Inferencia automatica de layout (2 dias)

Objetivo: remover necessidade de escolher banco/layout manualmente.

Entregas:
- mecanismo de score por layout
- fallback generico sem entrada manual
- metadados de confianca para observabilidade

Aceite:
- nenhum combobox/manual step necessario no fluxo de backend

## Fase 3 - OFX bonus + endpoint (1-2 dias)

Objetivo: gerar e disponibilizar OFX bonus ao final da analise.

Entregas:
- gerador OFX
- salvamento em storage temporario
- endpoint `GET /report/{analysis_id}/bonus-ofx`

Aceite:
- cliente baixa OFX valido para `analysis_id` ativo

## Fase 4 - Qualidade e validacao completa (1-2 dias)

Objetivo: garantir previsibilidade para release.

Entregas:
- testes unitarios parser/inferencia/gerador
- testes de integracao API para PDF
- validacao manual minima de API com requests reais

Aceite:
- suite de testes verde
- casos felizes e negativos cobertos

---

## Estrategia de inferencia (sem combobox)

1. Detectar candidatos de layout por anchors
- exemplos: cabecalhos de coluna, termos de saldo, padroes recorrentes do banco

2. Calcular score por layout
- cobertura de linhas parseadas
- consistencia de data e valor
- coerencia de sinais (entrada/saida)

3. Selecionar melhor perfil
- se score >= limiar: usar perfil
- se score < limiar: usar parser generico com regras mais tolerantes

4. Registrar confianca
- persistir score, layout escolhido e razao de fallback para evolucao incremental

---

## Plano de testes (TDD)

## Unitarios

- `test_pdf_parser.py`
  - parse de linha valida
  - parse de valor BRL com virgula/ponto
  - erro quando data/valor nao extraiveis

- `test_pdf_layout_inference.py`
  - escolhe layout com maior score
  - cai em fallback quando score baixo

- `test_ofx_generator.py`
  - gera OFX com `STMTTRN` e campos obrigatorios
  - preserva sinal e data corretamente

## Integracao

- `test_analyze_service_multiformat.py`
  - aceitar `sample.pdf` e produzir preview

- `test_analyze_and_report_api.py`
  - `POST /analyze` com PDF feliz
  - erro para PDF invalido

- `test_reconcile_api.py`
  - aceitar `bank_file` em PDF no fluxo de conciliacao

- novo teste para download OFX bonus
  - `GET /report/{analysis_id}/bonus-ofx` feliz
  - `404` para id expirado/inexistente

---

## Validacao manual minima de API

1. `POST /analyze` happy path com `PDF` real suportado
2. `GET /report/{analysis_id}` happy path
3. `GET /report/{analysis_id}/bonus-ofx` happy path
4. 1 caso negativo: PDF invalido/nao legivel

Quando houver mudanca em logica de conciliacao, repetir validacoes exigidas em `AGENTS.md`:
- match de transferencia interna
- match de estorno
- agrupamento de possivel duplicidade

---

## Riscos e mitigacoes

- Risco: PDF sem camada de texto (escaneado)
  - Mitigacao: detectar cedo e retornar erro orientativo no MVP; OCR fica em fase posterior

- Risco: grande variacao de layout entre bancos
  - Mitigacao: motor de score + fallback generico + backlog incremental por logs

- Risco: inconsistencias de valor/sinal
  - Mitigacao: validacoes de coerencia e comparacao entre preview e OFX gerado

- Risco: regressao em parsers atuais (`CSV`/`XLSX`/`OFX`)
  - Mitigacao: manter cobertura existente e adicionar testes de regressao multiformato

---

## Estimativa total

- Primeiro corte funcional: 7 a 9 dias uteis
- Corte com hardening e observabilidade: 9 a 11 dias uteis


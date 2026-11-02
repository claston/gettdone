# OFX-67 — Proteção reproduzível do conversor Free

Referência funcional: `c3cf02c4d4631ae591e2a59571dd9d1b557ec5f5`.
Escopo: testes, caracterização, ferramentas offline e CI. Nenhum arquivo em
`backend/app`, parser, YAML, OCR, frontend, quota, TTL ou migration foi alterado.
As OFX-68/69/70 continuam fora desta implementação.

## Diagnóstico anterior à implementação

O repositório já possuía 386 módulos de testes Python, além das suítes do PRO,
fixtures sintéticas e um gate diferencial em `scripts/check_pdf_compatibility.py`.
Não era necessário criar outro corpus nem outro pipeline de conversão.

| Área | Evidência existente | Lacuna da proteção anterior |
| --- | --- | --- |
| Parsing unitário | `test_pdf_*`, `test_csv_parser.py`, `test_xlsx_parser.py`, `test_ofx_parser.py`; fixtures `pdf_golden_samples.py` e `pdf_scenarios/*.json` | Goldens e regressões locais não congelavam todo o resultado observável entre versões |
| Pipeline e semântica | `test_conversion_pipeline.py`, `test_document_conversion_pipeline.py`, `test_structured_conversion.py`, `test_financial_document_events.py` | Gate anterior comparava sobretudo multiconjuntos de data/valor e identidade, perdendo ordem e descrição |
| OFX e FITID | `test_ofx_writer.py`, `test_convert_report_api.py`, `test_statement_account_exports.py` | Faltava comparação diferencial do OFX completo, FITID, conta e saldo |
| Excel/CSV | `test_export_artifact_service.py`, `test_convert_report_http_real_api.py`, `test_statement_account_exports.py` | Faltava snapshot semântico de todas as abas/células das duas planilhas e do CSV |
| Revisão/persistência | `test_convert_edits_api.py`, `test_convert_edits_http_real_api.py`, `test_storage_service.py`, `test_s3_analysis_storage.py`, `test_statement_review_http.py` | Não existia baseline congelada das linhas persistidas junto às exportações |
| OCR | `test_pdf_ocr.py`, `test_textract_*`, `test_ocr_evidence_preservation.py`, testes bancários OCR e `test_synthetic_pdf_corpus.py` | Evidência sintética não mede qualidade/latência de reconhecimento real; testes Tesseract opcionais |
| Free/API/UX | `test_convert_api.py`, `test_convert_and_auth_api.py`, `test_conversion_upload_sse.py`, `test_conversion_feedback_http.py`, `test_frontend_cookie_session_security.py`; smokes Playwright existentes | Sem novo ensaio visual ou carga distribuída nesta entrega |
| Recusa/limites/TTL | `test_pdf_text_extraction.py`, `test_document_preflight_service.py`, `test_conversion_capacity.py`, `test_storage_ttl.py`, `test_conversion_access.py` | Recusas e crashes podiam parecer equivalentes no gate antigo; faltavam entradas de limite/senha na baseline |
| Desempenho | `backend/scripts/pdf_baseline_benchmark.py`, `pdf_ocr_benchmark.py`, `evaluate_synthetic_pdf_corpus.py` | Faltava relatório estruturado pré/pós incluindo persistência/exportação, CPU e RSS |

Os testes existentes continuam sendo gates de API, revisão, quotas, expiração e
comportamento de upload/download. O novo snapshot complementa essas garantias.
`POST /analyze` já está desativado e responde 405: não o reativar para cumprir o
roteiro histórico de V1. O caminho atual é `/convert`, `/report/{id}` e
`/convert-report/{id}`; a suíte completa exercita servidores HTTP reais.

## Corpus e duas formas de validação

O gate reaproveita os 79 casos de `make_corpus()` e onze casos construídos com
fixtures/geradores existentes: vazio, inválido, senha, layout desconhecido, três
limites, três cenários aprovados do catálogo e uma transferência de descrição
multilinha com 128 caracteres. São **90 casos**, sem download de
extratos nem acesso a produção. O PDF com senha contém apenas uma página vazia;
seu sal criptográfico pode mudar entre execuções, sem mudar o caso lógico. Cada
execução registra o hash exato do corpus efetivamente entregue aos dois workers.

| Cenário | Cobertura |
| --- | --- |
| Texto nativo e colunas posicionadas | Fixtures de texto e `positioned_pdf`/`grid_pdf`, inclusive rotação; não são reproduções certificadas de extratos reais |
| OCR | 29 casos de evidências de linhas/palavras passam pelo leitor OCR real; o reconhecimento externo é desativado |
| Muitas páginas/períodos | 72 páginas, 12 meses, aberturas repetidas, paginação reiniciada, lacunas entre blocos e referência a páginas |
| Conta/documento | Multiconta Santander, relatórios financeiros Omie/XP e recusas por associação ilegível |
| Linhas/valores | Descrições quebradas/longas, referências, créditos/débitos, centavos, negativos, estornos, linhas repetidas legítimas e saldos intermediários |
| Revisão e recusa | Saldo/totais divergentes, período/cobertura incertos, valor ilegível, página adicional não recuperável, PDF inválido, protegido ou desconhecido |
| Limites | Preflight sintético: 11 páginas OCR, 251 páginas nativas, tamanho acima de 10 MiB; os testes existentes cobrem fronteiras, quotas e HTTP |
| Sem movimento | Evidência positiva, zero lançamentos e saldos de 150,00; nenhum movimento inventado |

**Correção financeira:** os resultados transcritos nas fixtures são independentes
do snapshot. O gate verifica datas/valores/sinais e multiplicidade em todos os
casos aplicáveis, revisão obrigatória e recusa; nos três cenários do catálogo,
também confere ordem e descrições esperadas, os movimentos exportados em OFX,
CSV e Excel e a presença/unicidade de FITID. O caso multilinha também confere a
descrição inteira e os saldos; o caso vazio confere saldos de origem.
Os testes financeiros existentes continuam cobrindo transferências, estornos,
duplicidades, saldos e exportações especializadas.

**Caracterização:** `backend/tests/fixtures/free_converter_baseline.json` congela
o comportamento da referência. Cada caso registra entrada, condições, estratégia,
resultado, linhas/IDs/ordem/proveniência, valores em centavos, saldos, métricas de
validação, warnings, dados persistidos e exportações. O gate também compara a
referência Git pedida contra a candidata, executadas em processos separados com
o mesmo corpus e o mesmo interpretador/dependências.

O snapshot não certifica exatidão financeira nem transforma defeitos anteriores
em regras de negócio. Os dois gaps já declarados no catálogo (`date_and_amount_formats`
e `tabular_credit_debit`) continuam nos seus testes de correção com o diagnóstico
original; não foram promovidos a goldens financeiros imutáveis.

## Execução e investigação de diferenças

Na raiz do repositório, usando o venv existente:

```powershell
backend\venv\Scripts\python.exe scripts/check_pdf_compatibility.py --base-ref <commit-referencia> --output-dir tmp/ofx67-check
```

O código da referência é extraído com `git archive`, sem checkout nem mudança da
branch. A aplicação candidata é copiada do workspace para outro diretório no
mesmo armazenamento temporário local: ambas as revisões têm localização
comparável, inclusive quando o workspace original usa OneDrive. Os únicos
arquivos de entrada são gerados pelas fixtures autorizadas.
Cada worker executa parsing → pipeline → `TempAnalysisStorage.save_analysis` →
exportadores de produção, usando armazenamento descartável. A comparação inclui
`report.xlsx`, `converted.xlsx`, `converted.csv` e OFX quando o documento permite.
Não há um exportador alternativo criado para os testes.

Perfil offline fixado nos dois workers: `TEXTRACT_ENABLED=false`,
`PDF_OCR_ENABLED=false`, `AI_RECOVERY_MODE=off`,
`CANONICAL_LAYOUT_CAPTURE_ENABLED=false`, `PDF_MULTI_ACCOUNT_ENABLED=true` e
`PYTHONDONTWRITEBYTECODE=1`. Isso permite exercitar multiconta sem serviços
externos; não representa uma alteração ou declaração dos defaults produtivos.
Os testes existentes de configuração/quotas/limites continuam protegendo esses
contratos fora do perfil sintético.

Artefatos em `--output-dir`:

- `corpus.json`: entradas exatas, transcrições esperadas e evidências OCR sintéticas.
- `baseline.json` e `candidate.json`: resultados por caso, entrada, timings e ambiente.
- `comparison.json`: commits, árvore da aplicação, hash do corpus, erros de correção,
  diferenças contra Git e contra o snapshot congelado. Cada diferença contém
  `case`, `path`, `before`, `after` e `change`.

O processo retorna 1 para qualquer diferença não aprovada, falha de correção,
caso ausente, baseline ausente, crash ou timeout do worker. Uma exceção inesperada
em uma entrada recusada não conta como recusa financeira válida. Repetições com
snapshots diferentes também falham. Relatórios preservados pela CI são sintéticos.

Normalizações catalogadas e limitadas:

- NFC/espaços em descrições de linhas/canônico; persistência e exportações ainda
  preservam seu conteúdo original, podendo detectar diferenças adicionais.
- Somente `total_ms`, `parse_ms`, `classify_ms`, `normalize_ms` e `reconcile_ms`
  em `pdf_processing_metrics` são excluídos da caracterização.
- XLSX é comparado por ordem/nome das abas, tipos, fórmulas e valores de células;
  metadados do contêiner ZIP e datas de criação do workbook ficam de fora.
- CSV é comparado por células, ignorando somente separadores de linha e registros
  físicos totalmente vazios, inclusive a variação CR/CRLF do Windows.
- `DTASOF` gerado pelo relógio é normalizado somente em extratos sem transações.
  Datas de movimentos, saldos, FITID e todas as demais linhas OFX são comparados.
- Dinheiro precisa ser representável exatamente em centavos: 20,001 falha em vez
  de ser arredondado para esconder a diferença.

Mudanças legítimas exigem análise do diff e aprovação explícita do proprietário.
Não basta adicionar um `correction_reason` genérico. Opcionalmente, `--approvals`
aceita uma lista JSON de aprovações **exatas**, cada uma contendo:

```json
{
  "reference_commit": "SHA completo da referência comparada",
  "candidate_app_tree": "git rev-parse HEAD:backend/app",
  "approved_by": "proprietário que aprovou",
  "approval_reference": "link para o aceite explícito",
  "reason": "evidência e justificativa da mudança",
  "difference": {
    "case": "identificador",
    "path": "$.campo",
    "before": 20000,
    "after": 2000,
    "change": "changed"
  }
}
```

Nenhuma aprovação foi concedida ou criada nesta entrega. A aprovação não dispensa
o oráculo financeiro e não autoriza mudanças em outro campo/valor/árvore. Código
de aplicação não commitado não pode usar esse mecanismo. Uma mudança aprovada
futura deve versionar o arquivo e a invocação CI com `--approvals`, mantendo os
diffs visíveis no relatório. As referências Git e congelada são verificadas
separadamente; aprovações precisam corresponder a cada referência aplicável.

Para revisar uma nova baseline, `--capture-baseline <arquivo-novo>` cria um
artefato exclusivo e recusa sobrescrita. Não executá-lo automaticamente ao falhar.
Após analisar/aprovar mudanças e validar as transcrições independentes, gerar a
nova evidência da revisão correspondente e revisar sua substituição em PR. A
baseline financeira nunca é regenerada a partir do resultado extraído.

## Desempenho reproduzível e limites de interpretação

```powershell
backend\venv\Scripts\python.exe scripts/check_pdf_compatibility.py --base-ref <commit-referencia> --repeat 5 --profile maquina-carga-controlada --performance-gate --output-dir tmp/ofx67-performance
```

O workflow manual `free-converter-benchmark.yml` executa a mesma comparação no
mesmo runner, com dez repetições por padrão. O gate funcional continua automático
em `ci.yml`; o desempenho é opcional para evitar atribuir ruído de runner a uma
mudança financeira. É obrigatório executar/analisar o benchmark antes do rollout
dos próximos incrementos.

Medições: p50/p95 interpolados de duração de parsing + pipeline + persistência e
exportação; CPU do processo nesses intervalos; tempo de geração/persistência;
bytes gerados; recusas e falhas técnicas. A releitura diagnóstica de workbooks e
construção de snapshots ficam fora desses tempos. Importação inicial e geração
do corpus também ficam fora. As repetições não descartam a primeira amostra.

Grupos separados: `native`, `ocr_evidence`, `native_large` (≥50 páginas) e
`preflight`. O relatório guarda todas as amostras por arquivo, evitando depender
somente da mistura global. RSS de pico é o máximo real **do worker inteiro**
(Windows `GetProcessMemoryInfo`, Unix `getrusage`), incluindo imports e diagnóstico;
não é alegado como pico individual de cada arquivo. CPU é tempo consumido, não
porcentagem instantânea nem CPU do OCR externo.

Orçamento local inicial: no máximo **+10%** no p95 total, CPU p95, exportação p95
por grupo e RSS do worker; nenhum aumento de falhas técnicas/timeouts. Exige
ambiente/perfil iguais e pelo menos cinco repetições. Decisões de preflight que
duram microssegundos têm resultado/falha protegidos, mas não orçamento relativo
de latência sujeito à resolução do relógio. Qualquer falha deve ser investigada
e repetida sob carga controlada, sem aumentar limiares para obter verde.

O timeout é um limite do processo do corpus (180 s × repetições); uma expiração
registra falha de infraestrutura e retorna 1. Não é uma medição de timeout HTTP
individual. O relatório concluído com `timeouts: 0` só afirma que esse limite não
foi atingido. O ambiente, versões relevantes e rótulo de perfil são registrados.

Não há SLO produtivo demonstrado por este benchmark. Antes de rollout, homologação
deve medir upload até download por HTTP real, p50/p95 por páginas/representação,
CPU/RSS do contêiner e processos filhos, filas/concorrência, taxa de falha técnica,
timeouts HTTP e 499 no proxy com denominadores. Usar OCR real e infraestrutura
comparável, sem misturar mudanças da OFX-63 com o refactor. Exigir zero diferença
financeira não aprovada e p95 ≤110% da referência em carga equivalente. O SLO
absoluto depende de capacidade/carga medidas; não se inventou um número produtivo.

## Baseline medida em 01/11/2026

Windows 11, Python 3.12.10, cinco repetições por caso, 450 conversões/recusas por
revisão. Ambas as revisões usam a mesma árvore de aplicação
`82caea2fbdaae1492bb4cd5696b7a2f2c4f64cc0`. Resultados: 28 sucessos, 35 revisões
e 27 recusas por passagem; zero diferenças financeiras/funcionais, zero falhas
técnicas e nenhum timeout do worker.

| Grupo | p50 referência/candidata (ms) | p95 referência/candidata (ms) | CPU p95 referência/candidata (ms) | Geração p95 referência/candidata (ms) |
| --- | --- | --- | --- | --- |
| Nativo, 285 amostras | 46,97 / 49,16 | 100,51 / 104,71 | 93,75 / 93,75 | 42,96 / 40,72 |
| Evidência OCR, 145 amostras | 113,15 / 115,90 | 175,84 / 189,18 | 156,25 / 153,12 | 39,69 / 50,08 |
| Nativo de 72 páginas, 5 amostras | 1.515,46 / 1.715,87 | 1.535,65 / 1.826,83 | 1.500,00 / 1.721,88 | 103,24 / 111,04 |

Pico RSS do worker: 124,59 / 124,41 MiB. Os números completos e bytes gerados
estão no relatório JSON; variações de bytes do contêiner XLSX não equivalem a
diferenças de células financeiras.

**A avaliação do orçamento final falhou:** p95 do PDF de 72 páginas passou de
1.535,65 para 1.826,83 ms; CPU p95 de 1.500,00 para 1.721,88 ms; geração p95 do
grupo OCR de 39,69 para 50,08 ms. Todos excedem +10%, mesmo com aplicação idêntica.
O ensaio anterior de 89 casos com `--performance-gate` retornou 1 por variação no
grupo OCR; o primeiro sinalizou geração em outros grupos. Os três ensaios foram
preservados no JSON. A captura final de caracterização retornou 0, pois não impõe
o orçamento de velocidade; `compare_performance` aplicado às suas amostras registra
essas falhas separadamente. Não é aprovação de performance ou evidência de mudança
introduzida no produto. Igualar fontes em temporários locais reduziu uma assimetria,
mas a variabilidade persistiu. Isso limita a confiabilidade
do benchmark local para aprovação de rollout. Nenhum limiar foi relaxado nem
amostra removida para obter sucesso; homologação com carga/disco controlados e
OCR real continua necessária. O gate funcional e o snapshot congelado passaram.

## Lacunas e preparo dos próximos incrementos

Faltam sucessos reais auditados e anonimizados, autorizados, com transcrição
completa independente; PDF apenas com contornos vetoriais sem camada de texto;
diversidade de qualidade de scanners, fontes vetoriais e
descrições reais imprevisíveis; reconhecimento OCR real com latência/custo; carga
do ambiente de produção e tráfego 499. Não se afirma que sintéticos substituem
essas amostras. Não se copiou nenhum documento pessoal para preencher a lacuna.

OFX-68 pode reutilizar este gate antes/depois da extração do serviço de comandos,
junto aos testes existentes de edição, exclusão/restauração, anotação, idempotência,
conflito e reabertura. OFX-69/70 deverão acrescentar os ensaios reais de dois
processos, PostgreSQL/S3, crash entre commit/artefato e rollback/feature flag por
análise. Esses testes e flags não foram antecipados. Free continua no legado;
uma futura análise canônica não pode voltar a receber escrita por writer legado.

Rollback desta entrega: reverter o commit de testes/ferramentas/CI. Não há dados,
schema ou comportamento produtivo a desfazer. A documentação da OFX-64 e as issues
OFX-47/63/68/69/70 foram consultadas; nenhuma delas foi implementada por efeito
colateral. O serviço de notas `get_agent_context` do conector Linear respondeu
“Tool not found”; a leitura das issues e do parecer arquitetural funcionou.

## Evidência da execução

A execução e as métricas finais são registradas em
`docs/free-converter-baseline-report.json`, no PR e na OFX-67. O relatório contém
apenas métricas e identificadores sintéticos, com referência ao corpus e ao commit.

Arquivos alterados/criados:

- `scripts/check_pdf_compatibility.py`: extensão do gate existente.
- `scripts/free_converter_guard.py`: snapshots, oráculos adicionais, comparação
  estrita, aprovação exata e medição offline.
- `backend/tests/test_free_converter_guard.py`: mutações deliberadas e pipeline real.
- `backend/tests/fixtures/free_converter_baseline.json`: caracterização por caso.
- `.github/workflows/ci.yml`: gate ampliado na execução existente e lint do helper.
- `.github/workflows/free-converter-benchmark.yml`: comparação manual de desempenho.
- `docs/free-converter-baseline-report.json`: métricas e resultados automatizados.
- `docs/free-converter-guard.md`: diagnóstico, operação, riscos e evidências.

Comandos e resultados de testes (todos os `pytest` fora do sandbox Windows):

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/test_free_converter_guard.py -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ofx67-red
# Falha inicial esperada: comparador ainda inexistente (TDD).

backend\venv\Scripts\python.exe -m pytest backend/tests/test_free_converter_guard.py backend/tests/test_pdf_compatibility_gate.py backend/tests/test_document_preflight_service.py backend/tests/test_statement_account_exports.py backend/tests/test_empty_statements.py -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ofx67-targeted
# 67 passed, 2 warnings in 9.72s.

backend\venv\Scripts\python.exe -m pytest -q -n 4 --dist=worksteal --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ofx67-full --junitxml=tmp/ofx67-full-suite.xml
# 3969 passed, 7 skipped, 2 xfailed, 8 warnings in 246.29s.

backend\venv\Scripts\python.exe -m pytest -q -n 4 --dist=worksteal --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ofx67-final-full --junitxml=tmp/ofx67-final-full-suite.xml
# Validação final: 3972 passed, 7 skipped, 2 xfailed, 8 warnings in 232.97s.

backend\venv\Scripts\python.exe -m pytest backend/tests/test_free_converter_guard.py backend/tests/test_pdf_compatibility_gate.py -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ofx67-final-unit
# 27 passed in 1.21s, incluindo os testes acrescentados após a coleta da suíte completa.

backend\venv\Scripts\python.exe -m ruff check backend
backend\venv\Scripts\python.exe -m ruff check scripts/free_converter_guard.py --config backend/pyproject.toml
backend\venv\Scripts\python.exe scripts/lint_frontend_text.py
backend\venv\Scripts\python.exe scripts/lint_frontend_navigation.py
# Todos aprovados; UTF-8/mojibake incluído.
```

Os diretórios `--basetemp` específicos foram removidos após validação, conferindo
seus caminhos absolutos sob `C:\Users\erica\AppData\Local\Temp`.

Os sete skips são seis ensaios com PostgreSQL descartável sem DSN configurado e
um ensaio com Tesseract ausente. Os dois xfails são os gaps já declarados no
catálogo; nenhum teste foi alterado para ocultá-los. Os warnings são da API de
eventos/lifespan já depreciada pelo FastAPI. A suíte PRO/Playwright não foi
reexecutada nesta entrega sem mudança de UI. O gate de cópias temporárias foi
validado também pelo comando completo de benchmark, contra Git e snapshot.

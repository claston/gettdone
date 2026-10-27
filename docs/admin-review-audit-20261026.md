# Motivos de revisão no painel: auditoria de 26/10/2026

A investigação usa o código versionado e consultas de leitura ao CloudWatch,
CloudFormation e metadados ECR. Não houve acesso ao banco de produção ou ao
Render; nenhum documento de cliente foi usado na validação da correção.

## Evidência nos logs

O grupo `/aws/lambda/gettdone-production-conversion-worker` registra dois jobs
em 25/10/2026, no fuso `America/Sao_Paulo`:

| Horário de conclusão | Job | Resultado | Duração |
| --- | --- | --- | --- |
| 08:51:36 | `job_b9b5ff1e43cf4426902bd983` | `completed` | 3.173 ms |
| 20:23:31 | `job_004f5e8a4b9444afbb1d9f0e` | `completed` | 4.309 ms |

O segundo corresponde exatamente ao evento informado,
`anon_evt_d376f93c200fd91f6008ce74`: o runtime deriva o ID dos primeiros
24 caracteres do SHA-256 do job. O ID de análise correspondente é
`an_d376f93c200f`. O primeiro job deriva `an_aadc800dd129` (ou
`anon_evt_aadc800dd1291f088a5bee26`, se a identidade for anônima).
A identidade e o motivo de revisão desse primeiro job não estão nos logs.

Os eventos `conversion_worker_result` contêm job, batch, trace, resultado,
duração e, nas falhas, código de erro. Não contêm confiança, cobertura, layout,
regra de qualidade ou motivos de revisão. Uma conversão `completed` pode
precisar de revisão: o sucesso técnico não prova qualidade suficiente.

No intervalo consultado, os totais de eventos foram:

| Dia local | completed | failed | already_terminal |
| --- | ---: | ---: | ---: |
| 23/10 | 167 | 51 | 1 |
| 24/10 | 24 | 2 | 0 |
| 25/10 | 2 | 0 | 0 |

São contagens de eventos do worker, sujeitas a tentativas/reprocessamentos e
sem as conversões inline da API. Não representam taxa de revisão, nem permitem
concluir regressão com base nos dois sucessos de 25/10. Evidências brutas
permanecem localmente em `tmp/admin-review-aws-20261026/`.

## Mudanças recentes e limites da atribuição

O commit `6501f758` (PR #479, 23/10/2026 às 23:38:39, horário de São Paulo)
alterou `QUALITY_RULE_VERSION` de `2026-10-20.v1` para `2026-10-23.v2`.
Essa versão adicionou `parser_requires_review` para PDFs com confiança do
parser `medium` ou `low`, mesmo quando layout, número de transações e alertas
não geravam revisão pela versão anterior. Isso pode aumentar a taxa de revisão
sem uma piora na extração. O motivo concreto de cada evento depende das
métricas efetivamente persistidas; os logs atuais não permitem confirmá-lo.

`_resolve_confidence_band()` também atribui `medium` a parsers fora da lista
de alta confiança, além de casos de cobertura reduzida, fallback ou conflitos.
Não se deve deduzir uma dessas causas apenas do nome do layout ou da confiança.

Uma reprodução com a fixture sintética já existente
`test_parse_pdf_transactions_parses_santander_credit_card_invoice_sections_without_mixing_headers`
produziu o layout Santander informado, cinco lançamentos, score de layout
`1.0`, zero alertas e zero inconsistências de saldo. O parser
`sectioned_credit_card_invoice` recebeu confiança `medium`; a regra v2 gerou
somente `parser_requires_review`. Isso comprova que esse layout pode pedir
revisão por política de confiança, mesmo sem falha de extração detectada.
Não comprova que o documento real teve essas mesmas métricas.

A stack do worker foi atualizada em 24/10/2026 às 10:53:34 e aponta para
`sha256:f4361bf0c812e0d2b85163aacdc539c6191c1f8620f22bed0710ba788932eaea`,
cuja tag ECR é `release-d7999024e084`. A candidata Debian da PR #488 não está
nessa stack. As permissões consultadas não permitem `lambda:GetFunction` nem
`lambda:GetFunctionConfiguration` da função de produção; portanto, o digest
foi confirmado na stack, sem comprovação adicional contra drift da função.
A versão efetiva da API não foi confirmada sem acesso ao Render.

## Correção do painel

O painel passa a carregar os campos já existentes `parser_confidence_band`,
`parser_coverage_rate` e `warning_types_json` para ambas as identidades.
O motivo mostra a confiança registrada, a cobertura quando disponível,
o score de layout e cada tipo de alerta, incluindo códigos ainda desconhecidos.
Captura canônica desabilitada permanece separada da qualidade da conversão.

As avaliações gravadas são preservadas, inclusive listas vazias de motivos.
O painel anterior podia combinar um status histórico com motivos reconstruídos
pela regra atual quando a lista gravada estava vazia. Agora, motivos ausentes
são identificados explicitamente. Apenas registros sem avaliação são
reconstruídos, usando também confiança/cobertura, com essa origem indicada no
texto. A leitura nunca grava um backfill no banco. Versão, origem e métricas
também ficam disponíveis na resposta administrativa e no CSV, com campos
adicionais compatíveis com os clientes existentes.

Não há alteração das regras de qualidade, do parser, de mensagens SQS ou
do schema. A correção melhora a explicação; não remove revisões por conveniência.

## Conferência na base pelo usuário

Executar manualmente `scripts/audit_admin_review_reasons.sql` no schema da
aplicação. O script inicia transação somente leitura, limita cada consulta a
20 segundos e termina com rollback. Ele seleciona somente metadados dos dois
jobs e agrega dias completos de 19 a 25/10 por identidade, tipo, layout e versão
da regra, com volumes e denominadores explícitos. Históricos sem avaliação
permanecem identificados como tal. O script não foi executado em produção
durante esta investigação.

Comparar primeiro os mesmos tipos/layouts entre versões. A coluna
`reviews_only_parser_confidence` ajuda a separar o novo critério de revisão
dos demais motivos. Confirmar os campos dos eventos e a versão efetiva da API
antes de atribuir uma causa específica ou afirmar regressão de extração.

Rollback da correção: reverter o commit da PR; nenhum dado precisa ser revertido.

## Validação local

- TDD: nove cenários novos falharam inicialmente pelas lacunas de diagnóstico
  e reconstrução; passaram após a correção, para identidades anônimas e cadastradas.
- Testes focados de painel, exportação e histórico: **21 passed**.
- HTTP real: sessão administrativa, motivos detalhados no painel e CSV **200**;
  conversão CSV sintética e download do relatório **200**; relatório inexistente
  **404**; `/analyze` continua **405**.
- Suíte completa: `backend/venv/Scripts/python.exe -m pytest backend/tests -q -n 4
  --dist=worksteal --tb=short --basetemp
  C:\Users\erica\AppData\Local\Temp\gettdone-pytest-admin-review-full`:
  **2971 passed, 2 skipped, 2 xfailed** em 154,10 s. Apenas avisos já existentes
  de depreciação do FastAPI. Temporários específicos removidos após a validação.
- Ruff, lint de navegação e lint de UTF-8/mojibake: aprovados.

# OFX-70 — Conversor PRO sobre a autoridade SQL

## Base e limite de entrega

Base: `e316e7e`, após a integração do PR #536 (OFX-69) em 02/11/2026,
12:15:47 UTC. Os checks consultados no GitHub estavam aprovados. Foram lidas
OFX-70, OFX-69, OFX-68, OFX-67 e a decisão arquitetural da OFX-64.

Esta entrega prepara implementação e evidências para revisão. Nenhuma coorte
produtiva foi habilitada, nenhum merge/deploy foi executado. S3 real, carga de
homologação, observabilidade operacional e autorização humana continuam sendo
gates de liberação. Não há Central Financeira visual nem ampliação de retenção.

## Autoridade e composição

`PostgresMovementRepository` continua sendo o único writer de movimentos
canônicos. A edição reaproveita `ReviewCommandService`; `CanonicalProService`
conecta autorização, flags, leitura e exportação às rotas existentes.

- O storage original guarda a barreira `canonical_pending`, reconhecida por
  todos os writers legados. Encontrá-la encaminha as rotas de relatório/edição
  ao SQL. Uma falha de SQL não seleciona outro storage como fallback.
- Lote e cliente leem o mesmo `analysis_id`, os mesmos UUIDs e a mesma versão.
  O JSON do job é uma projeção transacional protegida pelo trigger da OFX-69;
  o novo endpoint não o sincroniza por uma segunda escrita após o commit.
- O frontend conserva aliases estáveis para apresentação e envia `movement_id`
  e `expected_version`, além de versões dos movimentos. Posição só determina
  ordem de inserção. Clientes antigos podem enviar o alias estável e o token
  `expected_updated_at`: a resolução ocorre sob o mesmo lock SQL/CAS.
- Alteração financeira mantém a política existente de invalidar revisão humana
  e sugestões de conciliação. Evidência inicial, proveniência e histórico SQL
  permanecem protegidos pelos mecanismos da fase 2.

## Contratos

| Caminho | Comportamento canônico |
| --- | --- |
| `POST /convert-edits/{id}` e `/api/pro/convert-edits/{id}` | Aceita UUID, versão agregada e versões individuais. Responde com `authority`, `version`, `movement_ids`, `movement_versions`, além dos campos existentes. |
| `GET .../operations/{operation_id}` | Recibo confirmado em SQL; não gera arquivo nem altera versão. |
| `GET /api[/pro]/conversion-batches/{id}` | Lê a autoridade SQL para jobs canônicos. |
| `GET /api/pro/receipts/clients/{id}/workspace` | Mesma leitura SQL, preservando o lote de origem. |
| `POST /api[/pro]/conversion-batches/{batch}/jobs/{job}/review` | Comando SQL com operação e precondição; responde com a nova versão e versões dos movimentos. |
| `GET /convert-report/{id}` | Snapshot atual; `expected_version` opcional permite exigir a versão exibida. Overrides por GET são recusados: precisam de comando confirmado. |
| `GET /report/{id}` | Workbook derivado da mesma autoridade e versão. |
| Exportação ZIP por lote/cliente | `expected_versions` opcional em JSON; confere cada snapshot e revalida as versões antes de responder. Não omite um documento canônico cuja geração falhou. |

Contratos e serialização legados permanecem no caminho anterior. Campos novos
de resposta são adicionados somente às respostas canônicas. Free não consulta
o repositório canônico: segue HTML, parsers, cotas, OCR, TTL e storage existentes.

O contrato atual desabilita `POST /analyze` com 405. Não foi reativado para fazer
o roteiro histórico de validação parecer aprovado; a conversão vigente usa
`POST /convert`. O workbook `/report` é validado com HTTP real.

## Autosave, conflito e atualização de outras visões

O cliente aguarda a confirmação SQL para mostrar “Salvo”. Em falha de transporte,
conserva a operação e consulta seu recibo. Em 409, mantém as linhas locais e o
token antigo, bloqueando retries cegos. “Comparar revisão e rascunho” busca uma
nova revisão sem substituir o rascunho; “Reaplicar minhas alterações” reaplica
somente campos alterados localmente, preservando campos/linhas confirmados por
outro editor. Essa confirmação produz nova operação e ainda está sujeita a CAS.

O diálogo é modal nativo. O rascunho permanece na memória da sessão; não cria
retenção financeira em localStorage. O aviso de saída existente protege alterações
pendentes. Refresh após um autosave confirmado recupera a versão SQL.

Visões canônicas sem rascunho são atualizadas ao ganhar foco e a cada 15 segundos
enquanto a página está visível. Versões nunca recuam. Linhas em edição, conflitos
e operações em andamento conservam sua precondição. O polling adicional não é
executado para documentos legados/Free; seu custo no canário deve ser medido.

## Artefatos e falhas

`CanonicalArtifactService` reutiliza os exportadores OFX/CSV/XLSX da OFX-69.
Cada tentativa usa prefixo imutável por análise/versão/tentativa; somente um
manifesto compatível com a versão atual pode ser publicado. Download verifica
hash, permissão, expiração e versão novamente depois da leitura dos bytes.

409 significa snapshot alterado/suspenso; 503 de geração informa que a revisão
permanece salva e que o arquivo pode ser solicitado novamente. O frontend envia
as versões exibidas e atualiza seus links após save/revisão. Não há promessa de
transação distribuída PostgreSQL/S3. Um ZIP tem um vetor de versões de documentos;
não implementa um novo fechamento financeiro global.

## Flags persistentes e ingestão

Migration aditiva `20261102_02`, posterior à OFX-69:

| Controle | Escopo / padrão | Semântica |
| --- | --- | --- |
| `CANONICAL_PRO_COMPLETION_ENABLED` | API/worker, `false` | Instala o hook de conclusão; não é um seletor de autoridade de documentos existentes. |
| `canonical_pro_cohorts.ingestion_enabled` | Organização, `false` | Seleciona novas análises PRO elegíveis. |
| `canonical_pro_cohorts.shadow_enabled` | Organização, `false` | Exercita ingestão SQL em savepoint revertido e registra campos divergentes; sem barreira ou persistência canônica. |
| `canonical_pro_cohorts.read_enabled` | Organização, `false` | Valor inicial copiado para a análise na adoção. |
| `canonical_pro_cohorts.edit_enabled` | Organização, `false` | Valor inicial copiado para a análise na adoção. |
| `canonical_pro_cohorts.ui_enabled` | Organização, `false` | Reserva separada para futura experiência; nenhuma Central visual depende dela nesta entrega. |
| `canonical_analyses.read_enabled` | Análise, `false` | Desligar suspende leitura/exportação; nunca retorna ao JSON legado. |
| `canonical_analyses.edit_enabled` | Análise, `false` | Desligar suspende novos comandos, preservando SQL e recibos já confirmados. |
| `canonical_analyses.ui_enabled` | Análise, `false` | Não muda a autoridade nem desliga adapters do conversor atual. |

As flags não vêm do request/browser. Alterações operacionais são SQL restrito ao
administrador, com registro da mudança e IDs explícitos da coorte/análise. A
configuração da coorte só é copiada na adoção; congelar documentos existentes
exige alterar suas flags. Nenhum script desta entrega habilita produção.

Elegibilidade inicial: usuário ativo com plano pago `essencial`, `profissional`
ou `escritorio`, lote PRO, organização selecionada, contexto documental já
existente e extrato bancário novo. Trial, Free, relatórios especializados e
documentos sem contexto prévio continuam legados. **O primeiro canário deve
selecionar uploads que já possuam contexto cliente/job antes da conclusão.**
A associação automática posterior feita por Recebimentos não migra uma análise
que já foi exposta ao usuário. Ampliar a adoção a esse fluxo exige uma etapa
anterior de associação, sem migração silenciosa de documentos ativos.

O hook roda dentro da transação que conclui o job: grava a barreira de storage,
ingere SQL usando a mesma conexão, copia as flags e só então confirma o job.
Outra conexão não enxerga um job concluído ainda legado. Falha antes do commit
mantém o job sem conclusão e a barreira recuperável; repetir a conclusão com o
mesmo resultado retoma a adoção sem reprocessar/OCR ou consumir cota novamente.
Moeda segue a interpretação do exportador existente, inclusive os layouts Wise.

### Correções da revisão do PR #537

Os três apontamentos da revisão de `eab71e1` foram reproduzidos por testes antes
das correções:

1. **Retomada pelo worker:** a barreira agora grava, na mesma escrita atômica de
   `analysis.json`, um checkpoint imutável com o resultado concluído do pipeline
   e a identidade do job/lote/usuário. Antes de carregar o documento ou executar
   o pipeline, o worker PRO consulta esse checkpoint pelo ID determinístico da
   análise. O retry repete somente `mark_completed`, sem reconversão, OCR ou nova
   chamada de consumo de cota. O adapter S3 recupera o checkpoint mesmo com cache
   local vazio. Escopo e TTL original são verificados; expirados não são retomados.
   O checkpoint não é uma fonte editável e não entra no snapshot SQL: é removido
   junto ao storage de extração pelos mecanismos de retenção existentes.
2. **Conflito no cliente paginado:** a comparação busca o documento pelo lote de
   origem já conhecido, independentemente da página em que apareceu no workspace
   do cliente. A precondição e o rascunho continuam preservados até a reaplicação.
3. **Editar/desfazer sem alterações:** o autosave sem comandos libera `dirty`,
   permitindo que o próximo refresh adote linhas e versão de download remotas.

A leitura de checkpoints do worker permanece disponível com a ativação de
ingestão desligada: uma conclusão pendente fica suspensa, sem reconversão nem
publicação legada. O mesmo vale se a coorte for desligada ou passar a sombra.
Usuários Free não consultam checkpoints nem SQL canônico. Essas correções não
alteram schema ou defaults das flags. O teste de retomada usa pipeline, worker,
storage e PostgreSQL reais, injeta timeout depois da ingestão antes do commit e
verifica uma única execução do pipeline/consumo de cota. S3 usa o adapter real
com cliente simulado; a validação em bucket real continua pendente.

## Retenção e operação segura

O prazo efetivo continua sendo o menor prazo de análise, job e lote. Todas as
rotas SQL revalidam esse prazo; nenhuma recuperação reabre documento expirado.

Entrada de manutenção: `python -m app.workers.canonical_cleanup`, ou o `handler`
do mesmo módulo no executor operacional. Ela independe da flag de ingestão e
reutiliza a fila/locks de expurgo da OFX-69, removendo tentativas não publicadas,
objetos canônicos e a cópia legada cercada. A rotina só atua sobre expirados.
**Vincular e validar essa rotina no scheduler de homologação/produção antes do
canário**, juntamente com os lifecycles existentes para extrações interrompidas.
Esta entrega não cria um agendamento produtivo como efeito colateral.

Upgrade não altera tabelas/colunas anteriores, não faz backfill e mantém os
binários atuais compatíveis enquanto as flags estão desligadas. Downgrade é
permitido sem análises adotadas; com autoridade SQL existente, falha antes de
remover controles. Após primeira adoção, rollback significa interromper novas
adoções e, se necessário, tornar análises somente leitura. Manter um binário
compatível com SQL, comandos, barreiras e expurgo até expirar o último documento.
Nunca remover marcadores, copiar JSON antigo sobre SQL ou ligar writer legado.

## Gate de canário e monitoramento

1. Revisar PR, migrations, CI/scanner e relatórios diferenciais. Drenar API e
   workers antigos que desconhecem as barreiras. Confirmar backup e recuperação.
2. Executar sombra em organização de teste: zero diferença financeira, de ordem,
   sinal, saldo, moeda ou FITID. Conferir também arquivos com o gate de exportação.
3. Repetir ensaio integrado em S3 de teste, múltiplas instâncias, dois navegadores,
   falhas entre commit e geração, expiração e remoção de objetos.
4. Medir contra o mesmo corpus/carga: p95 não piorar mais de 10%; sem aumento
   relevante de CPU/RAM, falhas, timeout, HTTP 499 ou OCR/custo por conversão.
   Os limiares da OFX-67 não foram relaxados.
5. Com autorização humana, escolher uma organização paga e poucos documentos
   novos com cliente/conta conhecidos, incluindo centavos, sinais, duplicatas
   legítimas, múltiplos períodos e documento perto do TTL. Sem trial/Free.
6. Monitorar `canonical_save` (committed/conflict/failed, versão e duração),
   `canonical_export` (failed) e `canonical_shadow` (nomes de campos divergentes).
   Logs não incluem descrição, valor ou documento. Reutilizar métricas existentes
   do worker (`GettDone/Conversions`, Jobs/Duration), HTTP/proxy e plataforma.
7. Dashboards/alertas de p95, CPU/RAM, 499, falhas, 403/404/409, expurgo e custo
   precisam ser verificados em homologação. Testes locais não comprovam seu
   provisionamento nem o SLO produtivo.
8. Parar imediatamente por divergência financeira, artefato obsoleto, acesso
   cruzado ou perda de revisão; suspender por exceder os limites operacionais.
   Desligar adoção, preservar leituras SQL ou suspender o documento afetado,
   reparar/repetir exportação a partir do snapshot confirmado, mantendo auditoria.

## Matriz de aceite e evidências

Validação local em 02/11/2026, Windows, Python 3.12 e PostgreSQL 16 descartável.
Os detalhes reproduzíveis estão em `canonical-pro-ofx70-results.json`.

- Backend completo, serial: **4.022 passed, 7 skipped, 2 xfailed**, 1.030,76 s.
  Os seis skips de PostgreSQL foram cobertos em execução separada das migrations
  (**8 passed**). Permanece o skip por ausência de Tesseract; os dois xfails são
  limitações conhecidas do corpus sintético, não introduzidas nesta entrega.
- Revalidação SQL/HTTP após ajustes: **46 passed**. Depois da última correção da
  mensagem de conflito de revisão, a seleção OFX-70 passou novamente: **7 passed**.
  A suíte completa antecede esses ajustes finais; não é apresentada como uma
  segunda execução completa do mesmo commit.
- PRO: **51 arquivos, 257 testes aprovados**; ESLint, TypeScript, Ruff e lint de
  texto UTF-8 aprovados. A primeira repetição de Vitest recebeu `spawn EPERM` do
  sandbox; a execução fora dele passou.
- Build de produção e Chromium: dois editores usando HTTP/SQL reais, recuperação
  do rascunho e refresh; suítes existentes de workspace/navegação/organização e
  PDF/relatórios especializados aprovadas. O build usou cópia temporária fora do
  OneDrive após `EPERM` no diretório `.next`; não houve alteração de configuração.
- Gates diferenciais contra `c2ca8ab` (OFX-67): **90 conversões e 170 revisões**,
  zero divergência funcional/financeira ou de geometria. Goldens e tolerâncias
  permanecem intactos. O gate local não impõe performance de produção.
- HTTP real cobre edição, recibo, conflito 409, leitura lote/cliente, revisão,
  OFX/CSV/XLSX, workbook, ZIP, expiração e falha de geração 503 após commit.
  O roteiro vigente também cobre `/convert`; `/analyze` continua respondendo 405.

Comandos principais (na raiz, usando o Python do projeto; `pytest` é executado
fora do sandbox no Windows e com temporários exclusivos da tarefa):

```powershell
$env:CANONICAL_MOVEMENTS_TEST_DATABASE_URL = '<DSN PostgreSQL local descartável>'
cd backend
.\venv\Scripts\python.exe -m pytest -q --basetemp "$env:TEMP\gettdone-pytest-ofx70-full"
.\venv\Scripts\python.exe -m pytest tests/test_canonical_pro.py tests/test_canonical_movements_postgres.py tests/test_conversion_workspace_http_real_api.py tests/test_convert_edits_http_real_api.py -q --basetemp "$env:TEMP\gettdone-pytest-ofx70-final"
$env:OFX70_BROWSER_URL = 'http://127.0.0.1:3170'
.\venv\Scripts\python.exe -m pytest tests/test_canonical_pro_browser.py -q --basetemp "$env:TEMP\gettdone-pytest-ofx70-browser"
cd ..
.\backend\venv\Scripts\python.exe scripts/check_pdf_compatibility.py --base-ref c2ca8ab --output-dir tmp/ofx70-free
.\backend\venv\Scripts\python.exe scripts/check_review_compatibility.py --base-ref c2ca8ab --output-dir tmp/ofx70-review
npm.cmd --prefix pro run test:run
npm.cmd --prefix pro run lint
npm.cmd --prefix pro run typecheck
```

| Critério | Evidência / gate |
| --- | --- |
| Lote/cliente compartilham movimento e reabertura | Testes HTTP PRO com PostgreSQL e leitura das duas rotas. |
| Concorrência, retry e crash | Testes multiprocesso da OFX-69 e conclusão transacional da OFX-70. |
| Autosave, conflito, rascunho e refresh | Testes Vitest e teste Chromium de dois editores com HTTP/SQL reais. |
| Arquivo falha depois do commit | Fault injection de storage; SQL permanece confirmado e HTTP responde 503. |
| Exportação concorrente | CAS de publicação, revalidação de download e vetor de versões ZIP. |
| Centavos, sinais, datas, saldos, ordem e FITID | Gate exportador PostgreSQL com normalizadores da OFX-67 e gates diferenciais Free/revisão. |
| Expiração, permissões, tenants, clientes e contas | Casos SQL/HTTP da OFX-69 e guard comercial no adapter PRO. |
| Flags e rollback | Defaults desligados; sombra revertida; upgrade/downgrade/reupgrade e recusa de downgrade inseguro. |
| Free equivalente funcional/financeiro | 90 casos de conversão + 170 de revisão; zero divergência encontrada, sem alterar baseline. |
| Performance/custo produtivos | **Pendente** de ambiente/carga comparáveis e aprovação. |
| S3 real, scheduler e canário produtivo | **Pendentes** de homologação e autorização; nenhum rollout executado. |

O bucket S3 real não foi acionado nesta estação. A validação local usa PostgreSQL
descartável, filesystem real, HTTP real, processos independentes e Chromium;
testes específicos do adapter S3 existentes continuam sendo simulações. Por isso
a issue não é apresentada como integralmente aceita para rollout.

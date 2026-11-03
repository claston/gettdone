# OFX-74 — Central Financeira sobre movimentos canônicos

## Escopo e base verificada

Base `f006f8f`, atualizada de `origin/main`. Foram lidas as issues OFX-74,
65, 69, 70, 62, 66 e 73 e as instruções do repositório. Os PRs #536/#537
estão integrados, mas a inspeção confirmou três pré-requisitos incompletos:
identificação antes da adoção, associação de conta por movimento e reimportação
do mesmo documento entre lotes. O proprietário autorizou resolvê-los antes da
integração em 02/11/2026.

A configuração local examinada não tinha `DATABASE_URL`; o PostgreSQL instalado
exigia credenciais não configuradas. Portanto, não se afirma que migrations ou
coortes estejam ativas nessa base. A validação usa um cluster PostgreSQL 16
descartável, restrito ao loopback, com dados sintéticos. Nenhum rollout produtivo
ou acesso a dados financeiros reais foi executado.

## Autoridade, adoção e contas

`bank_movements` continua sendo a única autoridade financeira, com `DATE`,
`NUMERIC(18,2)`, UUID, versão, revisão e proveniência existentes. Não há nova
tabela de transações, ledger, parser ou processamento documental pela Central.

Em uma coorte previamente habilitada para ingestão **e UI**, `CanonicalCompletion`
reutiliza `ReceivedStatements.receive` na mesma conexão/transação da conclusão
do job. A evidência de identidade já produzida pelo pipeline associa o cliente
antes da adoção SQL. A chamada transacional não executa commit independente;
falha de adoção desfaz identificação/contexto/SQL junto com a conclusão. O
checkpoint de storage da OFX-70 continua permitindo retry sem reconversão.

`accounts_for_rows` associa somente contas documentadas em `account_links`:

- Uma origem com identidade confiável e uma única conta pode identificar suas
  linhas sem marcador individual de conta.
- Em documentos multiconta, exige evidência da linha, número compatível e todos
  os campos de agência/banco/cooperativa fornecidos. Mais de uma correspondência
  continua pendente.
- Conta selecionada manualmente para contexto/exportação não é distribuída pelas
  linhas. Documentos legados e análises já adotadas não recebem backfill silencioso.
- Linhas manuais sem associação comprovada permanecem em **Conta a confirmar**.

Fora dessa coorte, os controles da OFX-69/70 mantêm seu comportamento. Free não
entra no hook nem na consulta canônica.

## Reimportação e sobreposição

Documento de bytes idênticos (SHA-256 e formato), mesmo proprietário/organização
e mesmo cliente confirmado reutiliza a análise canônica original ainda válida.
O novo job referencia seu `analysis_id`, sem inserir movimentos. Lote/cliente/
Central leem o snapshot atual pelo adapter SQL, inclusive correções anteriores.
O contexto é serializado pelo lock da organização, evitando adoções concorrentes
da mesma origem. O storage da nova extração recebe a barreira antes da decisão.

O prazo efetivo do job repetido é limitado ao prazo original; um novo lote não
renova a análise ou suas revisões. Expirada a origem, a reimportação fica
indisponível e não impede abrir outros documentos válidos do lote. Um novo upload
depois da expiração pode ser uma nova extração, sem recuperar revisões expiradas.

**Documentos diferentes com movimentos na mesma data/conta não são fundidos.**
São sinalizados como possível sobreposição para revisão. O calendário/lista
preserva todas as ocorrências e proveniências; os totais desses dias e o total
mensal consolidado ficam indisponíveis. Isso evita somar como confiável uma
possível duplicação e também evita eliminar repetições legítimas por heurística.
Moedas distintas tampouco são somadas. Identidade global entre fontes diferentes,
diff de reprocessamento e confirmação de sobreposição continuam no domínio da
OFX-73; esta entrega não promete deduplicação automática arbitrária.

## Consulta e segurança

`GET /api/pro/financial-center/clients/{client_id}` aceita:

- `month=YYYY-MM` obrigatório;
- `account` opcional/repetível, no máximo 50; `unidentified` representa pendência;
- `day`, `pending`, `limit` (1–100; padrão 50) e `cursor` por data/UUID.

O backend resolve o proprietário pela sessão existente, valida usuário ativo,
plano PRO vigente, organização, cliente e contas. A consulta junta autoridade,
contexto, job e lote, verifica `read_enabled`/`ui_enabled` e usa o menor TTL.
Usa snapshot repetível, agregação SQL mensal e paginação dos movimentos; não
carrega toda a lista no browser nem faz consultas por movimento. Uma checagem
com snapshot fresco antes da resposta detecta revogação, expiração, revisão ou
suspensão durante a consulta. Mudança de fonte responde 409; indisponibilidade
responde 503 sem payload/log financeiro. Respostas são `private, no-store`.

Valores são strings decimais na API. A UI usa centavos `BigInt` para somar e
formatar, inclusive acima do limite inteiro seguro de `Number`. Datas bancárias
são strings de calendário, sem conversão de fuso. A moeda de cada registro é
preservada. Entradas/saídas incluem transferências, não são receita/despesa.

O plano canônico vigente aceita planos pagos `essencial`, `profissional` e
`escritorio`. Não há grant canônico de degustação implementado nessa base;
requisições sem esse direito são recusadas. Nenhuma permissão/plano novo foi
criado para contornar essa limitação. A experiência de degustação requer um
incremento explícito de autorização por documento, preservando suas 24 horas.

## Cobertura e experiência

A rota `/financial/{client_id}` reaproveita o CSS/hierarquia da OFX-65:
contexto de cliente, mês, filtro de conta, indicadores, calendário e lista.
Clientes ganha um link condicionado à flag. O protótipo continua disponível em
sua rota anterior. As áreas Conciliação/Fechamento/Relatórios são identificadas
como futuras e não oferecem operações simuladas sobre os dados reais.

Não se deduz cobertura do intervalo entre a primeira e a última transação.
Somente um documento explicitamente sem atividade, com período documental
conhecido e conta identificada, permite mostrar **Sem movimentações** no seu
recorte. Fora dessa evidência, a cobertura é desconhecida. Todas as contas
selecionadas precisam estar cobertas para afirmar inatividade; fontes legadas ou
suspensas impedem essa afirmação. A busca de evidências de inatividade é limitada
a 1.000 documentos; exceder isso pode conservar cobertura desconhecida, nunca
produzir falsa confirmação.

Documentos processados, revisão humana, cobertura, disponibilidade da origem e
conciliação financeira são dimensões distintas. Não há declaração de “tudo
conciliado”. Documentos legados/suspensos têm aviso de consulta parcial sem
expor seu conteúdo financeiro nessa API.

Abrir origem usa `/review/{batch}/{job}?return_to=...`, o editor existente. A
allowlist de retorno preserva cliente/mês/conta/dia/aba/pendência. Ao retornar,
atualizar ou recuperar foco, a Central consulta novamente a autoridade; não há
cache financeiro persistente. Falhas removem os valores visíveis. O vencimento
informado pela API agenda a remoção/renovação do snapshot na tela.

## Flags e validação local

| Controle | Padrão | Uso |
| --- | --- | --- |
| `PRO_FINANCIAL_CENTER_ENABLED` (API) | `false` | Habilita somente a rota de consulta. |
| `PRO_FINANCIAL_CENTER_ENABLED` (Next) | `false` | Habilita página e link; não concede acesso. |
| `NEXT_PUBLIC_API_URL` (Next) | não configurada | Deve estar configurada (`/` no proxy same-origin) para que Central e editor usem API real. |
| `BACKEND_API_URL` (Next) | não configurada | Destino server-only do proxy; inclui a nova rota. |
| `CANONICAL_PRO_COMPLETION_ENABLED` | `false` | Hook existente da OFX-70. |
| `canonical_pro_cohorts.ingestion_enabled` | `false` | Adoção de novos documentos. |
| `canonical_pro_cohorts.ui_enabled` | `false` | Opt-in adicional para identificação/contas/reimportação desta entrega. |
| `canonical_analyses.read_enabled/ui_enabled` | `false` | Consulta de cada análise, independentemente do frontend. |
| `canonical_analyses.edit_enabled` | `false` | Edição existente no conversor; Central não edita. |

Os controles da coorte são copiados na adoção; mudar a coorte não migra análises
existentes. Não habilitar coortes de produção por executar os testes.

Para validar em ambiente isolado: aplicar `alembic upgrade head`, selecionar uma
organização de teste e configurar nela os controles existentes. Executar API e
worker com o mesmo PostgreSQL/storage; buildar Next com API real e flag local.
Enviar documentos de um titular reconhecível, acessar Clientes, selecionar mês e
dia, abrir origem, salvar correção e voltar. Conferir o mesmo UUID/versão/valor no
SQL e na Central. Repetir com segunda conta/lote, origem idêntica, sobreposição,
revogação e expiração independente. Não habilitar ingestão global nem migrar
análises já expostas ao usuário.

## Migration e rollback

`20261103_01` substitui duas funções de proteção existentes para também proteger
jobs que referenciam uma autoridade original. Não cria cópia de transações,
tabela/coluna nova, backfill, DROP, RENAME ou mudança de tipo. A instalação das
funções usa uma transação curta de metadados, sem reescrever dados financeiros.

Aplicar migration e atualizar API/worker antes de habilitar a coorte/UI. Binários
anteriores continuam compatíveis enquanto não houver aliases. Depois da primeira
reimportação canônica, manter leitores compatíveis até as origens expirarem:
binários anteriores podem não representar corretamente a indisponibilidade de
um alias num lote ainda válido. O downgrade recusa aliases ativos; antes da adoção,
restaura as funções anteriores. Testes cobrem downgrade/reupgrade e recusa segura.

Rollback operacional: desligar a rota/UI e novas adoções da coorte; manter os
adapters SQL, checkpoints pendentes e expurgo da OFX-69/70. Não retornar escritores
de análises existentes ao JSON legado nem apagar revisões. Monitorar 409/503,
latência de consultas, pendências de ingestão e expiração, sem descrições/valores
em logs. Validar após deploy em organização de teste: autorização cruzada,
revisão refletida e documentos expirados indisponíveis.

## Evidências de validação

Antes das mudanças: backend **4.025 passed, 8 skipped, 2 xfailed**; frontend
**286 passed**. Os primeiros cinco testes novos falharam por ausência da consulta
e adoção automática, conforme esperado. Incrementos seguintes validaram SQL,
HTTP, contas, cobertura, aliases, TTL e regressão PRO. A suíte frontend ampliada
teve **290 testes aprovados**, com lint/TypeScript aprovados.

O build no OneDrive falhou com `EPERM` em `.next`; a cópia temporária fora dele
compila com `next build --webpack`. O teste Chromium detectou e motivou a correção
do link coberto pela área clicável do cliente. O build de integração deve definir
`NEXT_PUBLIC_API_URL=/`; sem isso o editor configurado pelo projeto é mock.

Comandos (pytest fora do sandbox Windows, Python do venv, basetemp exclusivo):

```powershell
$env:CANONICAL_MOVEMENTS_TEST_DATABASE_URL = '<PostgreSQL local descartável>'
cd backend
.\venv\Scripts\python.exe -m pytest -q --basetemp "$env:TEMP\gettdone-pytest-ofx74-full"
$env:OFX74_BROWSER_URL = 'http://127.0.0.1:3174'
.\venv\Scripts\python.exe -m pytest tests/test_financial_center_browser.py -q --basetemp "$env:TEMP\gettdone-pytest-ofx74-browser"
cd ..
npm.cmd --prefix pro run test:run
npm.cmd --prefix pro run lint
npm.cmd --prefix pro run typecheck
.\backend\venv\Scripts\python.exe scripts/check_pdf_compatibility.py --base-ref f006f8f --output-dir tmp/ofx74-free
.\backend\venv\Scripts\python.exe scripts/check_review_compatibility.py --base-ref f006f8f --output-dir tmp/ofx74-review
```

`POST /analyze` já está desabilitado (405); não foi reativado para cumprir um
roteiro antigo. Os testes HTTP exercitam o contrato vigente e `/report`, além da
consulta, autorização, erros, save/reabertura e exportação PRO.

O Chromium validou **Clientes → Central → dia → editor real → save → retorno à
Central com a descrição corrigida**, preservando cliente/mês/dia; também verificou
ausência de overflow horizontal em 390 px. Resultado: **1 passed**.

Os gates diferenciais contra `f006f8f` passaram: **90 conversões e 170 revisões,
zero divergências financeiras/funcionais e de geometria**. Não foram alterados
goldens, tolerâncias ou thresholds. O gate de performance produtiva não foi
acionado neste ambiente.

Medição sintética local: 1.000 movimentos, página de 50 registros, cursor para a
próxima página; totais exatos de 5,00 de entradas e 5,00 de saídas. Consulta em
**919,63 ms**, incluindo conexões e revalidação. É uma observação sob carga local,
não um SLA nem uma comparação controlada com produção. O caso adicional com
dois tenants e clientes distintos passou sem misturar valores.

A execução completa terminou em **4.034 passed, 9 skipped, 2 xfailed e 1 failed**
em 1.194,63 s. A única falha foi `httpx.ReadTimeout` no caso Free `bb_pending`,
durante conversão HTTP com limite existente de 20 s. O módulo inteiro foi
reexecutado sem validações concorrentes: **9 passed em 20,37 s**, sem alterar
código, timeout ou expectativas. Portanto, o timeout não foi reproduzido; não
se afirma que a execução completa original tenha sido inteiramente verde.

O módulo da Central no estado final, incluindo o teste adicional de isolamento
e a correção da evidência de inatividade (`statement_no_activity=1` e invalidação
após inserção manual), passou com **11 passed em 104,00 s**. A execução completa
havia iniciado antes desses últimos refinamentos; eles foram revalidados nesse
módulo. Os basetemps e o cluster descartável são removidos após a validação.

## Arquivos principais

### Correção da revisão: reassociação de contas

O apontamento no PR #539 foi reproduzido: um documento multiconta podia ter
movimentos associados às suas contas, mas contexto documental sem conta única.
`OrganizationService.save_account` verificava apenas esse contexto e permitia
mudar a conta para outro cliente, tornando o filtro da Central inconsistente.

A proteção agora consulta também `bank_movements`, incluindo registros
logicamente excluídos ou expirados ainda retidos. A alteração de conta adquire o
lock da organização antes de ler os vínculos, serializando-a com a adoção
canônica. Se a reassociação ocorrer antes da adoção, o guard existente da
ingestão recusa atomicamente vínculos de origem que ficaram desatualizados.
Renomear contas mantendo o cliente e reassociar contas sem vínculos continuam
permitidos. SQLite e PostgreSQL sem a tabela canônica preservam as regras
legadas. Não há nova migration, backfill ou modificação de dados existentes.

Regressões cobrem duas contas sem conta única no contexto, serviço e PATCH HTTP
real, filtro por conta após a recusa, renomeação permitida, histórico retido,
reassociação anterior à adoção e duas conexões concorrentes. Os módulos Central
e organização tiveram 18 testes aprovados; o caso concorrente adicional passou.

Após a retomada, a suíte backend completa passou com **4.041 passed, 8 skipped,
2 xfailed em 998,69 s**, incluindo PostgreSQL e o teste de upgrade/rollback da
organização. Ruff, varredura UTF-8 e `git diff --check` também passaram. Evidências
locais preservadas em `tmp/ofx74-account-full.log` e `tmp/ofx74-account-full.xml`.
O resultado da execução interrompida não foi utilizado como evidência de sucesso.

### Mapa de arquivos

Arquivos principais para revisão:

- `backend/app/application/financial_center.py` e
  `backend/app/routers/financial_center.py`: consulta SQL e contrato HTTP.
- `backend/app/application/canonical_identity.py`, `canonical_pro.py`,
  `received_statements.py` e adapter `canonical_movements.py`: identificação,
  conta por evidência e adoção/reimportação na autoridade existente.
- `backend/app/routers/canonical_pro.py`, `conversion_batches.py` e
  `received_statements.py`: snapshots atuais e indisponibilidade de aliases.
- `backend/alembic/versions/20261103_01_canonical_source_aliases.py`: proteção
  de escrita e contexto dos aliases, com downgrade condicionado.
- `pro/src/components/financial/live-financial-center.tsx`,
  `pro/src/data/financial-center-service.ts` e rota `financial/[clientId]`:
  calendário, listagem, filtros, precisão decimal, acesso e atualização.
- `received-statements-page.tsx`, `workspace-screen.tsx`, `review-return.ts`,
  configuração de proxy/visibilidade e `.env.example`: entrada/retorno e flags.
- `backend/tests/test_financial_center*.py` e testes próximos aos componentes
  frontend: integração SQL/HTTP, jornada de navegador e comportamento da UI.

## Próximas etapas

- OFX-73: identidade entre fontes diferentes, confirmação de sobreposição e diff
  de reprocessamento, sem confundir possíveis repetições com duplicatas provadas.
- Autorizações canônicas específicas da degustação e reassociação versionada de
  contas em documentos já adotados.
- Edição na Central pelo comando único existente; exportação do recorte;
  lançamentos independentes/conciliação; fechamento OFX-61.
- Retenção/preço permanecem na OFX-66. S3 real, carga de homologação e autorização
  humana de rollout continuam pendentes. Nenhum SLA produtivo é inferido do teste local.

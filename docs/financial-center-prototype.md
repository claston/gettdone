# Central Financeira — protótipo OFX-65

Implementado em branch a partir de `origin/main` `f3638c5`, em 02/11/2026.
O objetivo é validar a jornada operacional com dados fictícios. Nenhuma API,
tabela, migration, política de retenção, preço ou cota foi alterada.

## Executar e demonstrar

```powershell
cd pro
$env:PRO_FINANCIAL_CENTER_DEMO = 'true'
npm.cmd run dev -- --hostname 127.0.0.1 --port 3185
```

Abra `http://127.0.0.1:3185/demo/financial/clients`. O protótipo é autossuficiente,
sem backend e sem sessão real. O shell usa uma identidade explicitamente fictícia
sem escrever no cache de autenticação. A flag é opt-in no servidor; desligada,
as rotas retornam 404 e os acessos no PRO desaparecem. Reinicie o servidor ao
alterá-la. Em deploy, faça novo build para aplicar também a visibilidade do shell.
Não é uma flag de autorização financeira ou de adoção canônica.

Jornada: **Conversões → Clientes → explorar demonstração → selecionar cliente →
Calendário → selecionar dia → Lançamentos → abrir no revisor → voltar à Central**.
As áreas Conciliação, Fechamento e Relatórios preservam cliente, mês e contas.
O caminho de Clientes reais para o revisor continua igual; dados fictícios nunca
são apresentados sob o nome de um cliente real.

O atalho de primeira conversão abre um cliente já reconhecido, com uma conta e
um documento fictício cobrindo a primeira quinzena. Essa etapa simula o resultado
do reconhecimento, sem novo formulário. Upload, reconhecimento e concessão real
de 24 horas permanecem nos fluxos atuais ou nos gates futuros; não foram simulados
como serviços disponíveis em produção.

## Rotas e componentes

| Rota | Comportamento |
| --- | --- |
| `/demo/financial` | Redireciona para os clientes demonstrativos. |
| `/demo/financial/clients` | Reutiliza `ReceivedStatementsPage`, com serviço fictício tipado e identidade não editável. |
| `/demo/financial/[clientId]` | Cinco áreas da Central; URL guarda mês, contas, aba e dia selecionado. |
| `/demo/financial/[clientId]/review/[conversionId]` | Reutiliza `WorkspaceScreen`, autosave, grid, visualizador PDF e navegação entre documentos. |
| `/dashboard`, `/conversions`, `/review/*`, `/client-review/*` | Fluxos existentes preservados; apenas acessos opcionais à demonstração em shell/Clientes. |

Criados: `components/financial/financial-center.tsx`, CSS module, `demo-shell.tsx`,
`financial-demo-review.tsx`, `domain/financial-center.ts`, `data/financial-demo.ts`
e `financial-demo-pdf.ts`. Reutilizados: `AppShell`, `ReceivedStatementsPage`,
`WorkspaceScreen`, `TransactionGrid`, `RealDocumentViewer`,
`createMockWorkspaceRepository`, `reviewReturnTo`, botões e tokens visuais do PRO.
O menu móvel do shell agora abre os mesmos destinos de navegação; continua oculto
no desktop. O revisor recebeu apenas um construtor opcional de URL, preservando
o caminho original por padrão.

## Autoridade e separação de dados

`FinancialMovement` é uma projeção de leitura em centavos BRL, com ID estável e
origem documental. Não há editor de movimentações na Central. Cada cenário injeta
um `Batch` fictício no repositório mock já existente; esse mesmo objeto autoritativo
em memória é usado pelo revisor e relido ao retornar à Central. Não há cópia
editável paralela, armazenamento no navegador ou envio das operações a APIs reais.

O PDF fictício é gerado a partir da fixture original, antes das correções. A
correção muda o lançamento, preservando a evidência original. O PDF usa o
visualizador existente e contém um aviso explícito de demonstração.
Corrigir um documento invalida seu aceite na demonstração; a nova versão permanece
pendente até a confirmação explícita pelo revisor. Essa opção do repositório mock
é habilitada somente nos cenários da Central.

As fixtures e o adapter mock estão em `data/financial-demo*`; seletores e contratos
de leitura ficam em `domain/financial-center.ts`. `FinancialScenario` é o envelope
da demonstração, **não um DTO aprovado para o backend**. Um adapter real deverá
consultar o snapshot SQL autorizado, preservar IDs/versões/contas e obter a cobertura
dos blocos documentais. Nunca alimentar uma nova fonte de escrita com JSON de jobs.

## Decisões de UX e integridade

- Calendário calcula a união dos intervalos documentais disponíveis por conta.
  Um dia só tem cobertura suficiente quando todas as contas selecionadas o cobrem.
  A primeira e a última transação não provam cobertura. Dia sem movimentos,
  com movimentos, com pendências e sem cobertura têm sinais distintos.
- Dias com cobertura insuficiente podem conter movimentos. Eles permanecem visíveis,
  com aviso de incompletude. Mudar de mês não move nem replica lançamentos.
- Totais são créditos/débitos provisórios. Transferências e estornos conservam as
  duas pernas; repetições legítimas e possíveis duplicidades não são descartadas.
  Não são calculados saldos, receitas, despesas contábeis ou DRE.
- Filtros de data, descrição, direção e revisão se combinam com as contas do
  contexto. Pendências abrem o subconjunto pertinente. Origem indica arquivo e
  página, com acesso ao documento no revisor.
- Conciliação é uma fila de cenários, não um motor de matching. Transferência,
  estorno, ausência de contraparte, duplicidade e conflito são apresentados como
  sugestões/pendências. Contraparte fora das contas selecionadas fica explícita.
  Não há confirmação automática ou integração NF-e/Open Finance/IA.
- Fechamento exige contas explicitamente confirmadas e ao menos um extrato válido
  no mês. Extrato documentado sem movimentos é permitido; mês sem documento não é.
  Cobertura parcial e pendências operacionais exigem justificativa vinculada ao
  contexto. Conflito de conta, duplicidade e lançamento suspeito bloqueiam mesmo
  com justificativa. Alterar mês/contas invalida a conclusão simulada.
- Conclusão/reabertura são somente estado da tela. A justificativa aparece no
  relatório; não há snapshot financeiro persistido ou certificação fiscal.
- Expirados mostram apenas referência histórica fictícia: sem conteúdo, valores,
  cobertura válida ou link para PDF. Não se presume retenção financeira estendida.
- CSV real de demonstração inclui recorte, origem, revisão, resumo por conta,
  entradas/saídas, pendências e ressalva. UTF-8 com BOM, delimitador `;`, campos
  textuais protegidos contra fórmulas. Abre no Excel. XLSX consolidado fica
  explicitamente em preparação; as exportações existentes do conversor não mudam.
- Recarregar reinicia os dados simulados. Conclusão local não é recuperada após
  sair da tela. O escopo da URL volta do revisor, mas filtros textuais temporários
  são reiniciados. Calendário/tabela usam rolagem interna em telas estreitas.

## Cenários reproduzíveis

| ID | Demonstração |
| --- | --- |
| `single` | Cliente, conta e extrato únicos, mês documentado. |
| `multiple` | Duas contas, dois documentos de lotes distintos, transferência e estorno sugeridos, pagamento sem correspondência. |
| `partial` | Primeira quinzena documentada; fechamento possível apenas com ressalva. |
| `review` | Descrição suspeita, acesso à correção no revisor, bloqueio de fechamento. |
| `expired` | Referência histórica; conteúdo financeiro/PDF indisponíveis. |
| `duplicates` | Duas ocorrências preservadas; bloqueio de fechamento. |
| `divergence` | Associação de conta em conflito; ressalva não dispensa integridade. |
| `empty` | Extrato legítimo sem movimentos, cobertura completa. |
| `trial` | Primeira conversão fictícia, cliente reconhecido, uma conta, mês parcial. |

## Pesquisa arquitetural e estado das dependências

Consultados no Linear: [OFX-65](https://linear.app/ofxsimples/issue/OFX-65),
[OFX-64 e seu parecer](https://linear.app/ofxsimples/issue/OFX-64),
[OFX-62](https://linear.app/ofxsimples/issue/OFX-62),
[OFX-70](https://linear.app/ofxsimples/issue/OFX-70),
[OFX-61](https://linear.app/ofxsimples/issue/OFX-61),
[OFX-66](https://linear.app/ofxsimples/issue/OFX-66), além de OFX-67/68/69.
O parecer é o documento Linear `76693a04-b894-42c3-aa8b-260923ccbade`,
“OFX-64 — Avaliação arquitetural AS-IS/TO-BE da Central Financeira”.

As PRs #533 (gates Free), #535 (comandos), #536 (canônico SQL) e #537
(integração PRO) já constam na história da main examinada. Não havia PR aberto
na consulta inicial. Isso não comprova implantação/ativação em produção.
No Linear, OFX-67/68/69 estavam em andamento e OFX-70 em backlog; o status da
issue não foi usado para ignorar código já integrado. Consulte também os
[limites de rollout da OFX-70](canonical-pro-ofx70.md).

| Área | Já disponível | Dependência para Central real |
| --- | --- | --- |
| Identificação/Clientes | Recebimentos, reconhecimento, `received-statements-service`, `/api/pro/receipts`, conversor por cliente. | Reutilizar entidades autorizadas; não criar clientes fictícios para documento ambíguo. |
| Revisão e exportação por documento | Serviço de comandos, adapter HTTP do workspace e integração canônica da OFX-70. | Gates de performance/S3/canário e coorte autorizada. |
| Autoridade SQL | OFX-69 e OFX-70 integradas em main; CAS, versões, auditoria e artefatos derivados. | `CANONICAL_PRO_COMPLETION_ENABLED` e flags por coorte/análise começam desligados. Nenhuma foi alterada. |
| Visão mensal unificada | Modelo de leitura demonstrativo nesta PR. | Consulta SQL por cliente/conta/data, cobertura por bloco, paginação e agregação autorizadas. Não há endpoint mensal real conectado aqui. |
| Conciliação | Heurísticas existentes do conversor. | Relações versionadas com evidência e invalidação após correções; heurística não equivale a aceite. |
| Fechamento | `StatementWorkService` e APIs `/api/pro/works`, telas legadas preservadas/ocultas. | Reutilizar serviço após consistência com snapshot canônico e decisões da OFX-61; não criar um segundo backend. |
| Relatórios | Exportadores OFX/CSV/XLSX do conversor. | Exportação consolidada sob snapshot/versão e autorização do conjunto completo, sem reaproveitar somente a página visível. |
| Degustação | Demo do revisor; identificação do PRO pago. | Grant real por documento/operação, prazo mínimo entre concessão e origem, revogação e autorização antes dos agregados. OFX-70 exclui trial da adoção inicial. |
| Expiração financeira | TTL vigente por origem, sem extensão. | Decisão explícita da OFX-66 antes de conteúdo pós-expiração, preços, cotas ou novos prazos. |

## Próximos incrementos

1. Concluir os gates de homologação/rollout OFX-67/70 sem relaxar tolerâncias Free;
   verificar leitura canônica e elegibilidade do cliente reconhecido após conversão.
2. Adicionar consulta canônica de cliente/mês/contas e cobertura por blocos, com
   autorização antes de somar/paginar, expiração, IDs/versões e recusa de ambiguidades.
3. Implementar adapter de leitura substituindo fixtures, sem fallback silencioso
   para mocks. Revalidar após revisão; demonstrar duas abas e conflito de versão.
4. Conectar evidência e exports aos contratos existentes, com snapshot consistente,
   expiração e proteção de fórmulas; entregar XLSX consolidado.
5. Implementar relações de conciliação versionadas e confirmação explícita com
   evidência; invalidar sugestões/aceites quando seus movimentos mudarem.
6. Evoluir o serviço de fechamento existente para snapshots canônicos, confirmação
   de escopo, ressalvas, bloqueios e reabertura, conforme OFX-61.
7. Entregar grant real da degustação 24h, limitado à conversão concedida; testar
   ausência de acesso indireto a outros documentos/contas do mesmo cliente.
8. Tratar retenção/preços somente após decisão da OFX-66. Sem extensão implícita.

## Validação e evidências

O roteiro `scripts/smoke_financial_center.py` usa Chromium, navega pelo shell e
Clientes, abre o PDF, corrige pelo revisor, retorna e confere a mesma alteração,
testa filtros, bloqueios, conclusão com ressalvas, CSV, expirados e mobile. Exige
`PRO_FINANCIAL_CENTER_DEMO=true`; não faz requisições a `/auth/` ou `/api/pro/`.

```powershell
npm.cmd --prefix pro run test:run
npm.cmd --prefix pro run lint
npm.cmd --prefix pro run typecheck
npm.cmd --prefix pro run build
.\backend\venv\Scripts\python.exe scripts/smoke_financial_center.py
.\backend\venv\Scripts\python.exe -m ruff check backend scripts/smoke_financial_center.py
.\backend\venv\Scripts\python.exe scripts/lint_frontend_text.py
.\backend\venv\Scripts\python.exe scripts/lint_frontend_navigation.py
```

Resultados em 02/11/2026:

- Backend completo: **3.993 passed, 40 skipped, 2 xfailed**, 1.147,29 s. Executado
  de `backend` com `venv\Scripts\python.exe -m pytest -q --basetemp
  C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ofx65-full`, fora do sandbox.
  Os skips/xfails são explicitados; esta execução não certifica os gates pendentes
  de PostgreSQL/S3, performance ou rollout produtivo. Nenhum código backend mudou.
- PRO completo: **55 arquivos, 277 testes aprovados**. Após o ajuste final da
  invalidação de revisão, a seleção afetada passou novamente: **17 testes**.
- ESLint e TypeScript: aprovados, sem erros; Ruff: `All checks passed!`.
  Lints existentes de texto/navegação: aprovados. Varredura adicional de **203
  arquivos UTF-8** de `frontend` e `pro/src`: nenhum mojibake. Ocorrências legítimas
  de `Ã` em palavras como `DEMONSTRAÇÃO` não são corrupção de texto.
- Prettier dos arquivos frontend alterados: `All matched files use Prettier code style!`.
- Build de produção: aprovado numa cópia temporária fora do OneDrive após `EPERM`
  ao limpar `.next/static` no checkout. Nenhuma dependência ou configuração de build
  foi alterada para contornar o problema. Rotas da Central são dinâmicas.
- Chromium contra o build de produção: fluxo completo aprovado, desktop 1440 px
  e mobile 390 px, sem overflow da página, com menu móvel e PDF/CSV reais fictícios.
  Nenhuma requisição a APIs financeiras ou autenticação reais.
- HTTP: flag desligada retorna **404** em Clientes, Central e revisor demonstrativos.
  Ligada: Clientes **200**; cliente desconhecido, documento expirado e documento de
  outro cenário **404**. APIs backend não foram alteradas nesta entrega.

Capturas versionadas (todas com dados fictícios):

| Tela | Captura |
| --- | --- |
| Clientes | [Desktop](images/ofx65/clients-desktop.png) |
| Calendário | [Desktop](images/ofx65/calendar-desktop.png) · [Mobile](images/ofx65/calendar-mobile.png) |
| Lançamentos | [Desktop](images/ofx65/movements-desktop.png) · [Mobile](images/ofx65/movements-mobile.png) |
| Conciliação | [Desktop](images/ofx65/reconciliation-desktop.png) |
| Fechamento | [Desktop](images/ofx65/closing-desktop.png) |
| Relatórios | [Desktop](images/ofx65/reports-desktop.png) |
| Revisor reutilizado | [Desktop](images/ofx65/reviewer-desktop.png) |
| Primeira conversão | [Desktop](images/ofx65/trial-desktop.png) |

O CSV e o resultado do roteiro ficam em `tmp/ofx65-browser/`. Resumo reproduzível
da validação em [financial-center-prototype-results.json](financial-center-prototype-results.json).

## Compatibilidade e rollback

Nenhuma migration ou operação manual sobre dados. Parsers, Core Free, backend,
TTLs, grants, cotas e tabelas legadas preservados. Os defaults das props novas
mantêm as rotas existentes. Não houve merge, deploy ou ativação de coortes.

Rollback: desabilitar `PRO_FINANCIAL_CENTER_DEMO`, reconstruir/reiniciar o PRO,
ou reverter esta PR. Não há dados financeiros novos para migrar ou apagar.

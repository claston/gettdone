# Relatorio de conformidade LGPD - OFX Simples

Data: 2026-05-15
Escopo: auditoria tecnica do repositorio `gettdone` para orientar implementacao.
Aviso: este documento nao substitui parecer juridico; ele traduz riscos tecnicos e de produto em backlog executavel.

## Resumo executivo

O OFX Simples trata dados pessoais e financeiros de alto impacto pratico: extratos bancarios, lancamentos, nomes, e-mails, WhatsApp, CPF/CNPJ, identificadores Google, IP/user-agent de sessao, historico de conversoes e dados administrativos. A arquitetura ja tem bons pontos: TTL de artefatos temporarios, limite de upload, cookies `HttpOnly` para a sessao nova, refresh token hashado, validacao de baseline em producao, CORS configuravel, HSTS em producao e headers basicos de seguranca.

Mesmo assim, o site ainda nao esta maduro para uma declaracao forte de conformidade LGPD. Os principais problemas sao: politica publica incompleta para o nivel de tratamento observado, afirmacoes de marketing dizendo que dados "nao sao armazenados" enquanto o backend salva artefatos/metadados por TTL e historico em banco, Google Tag Manager carregado antes de consentimento granular, tokens legados em URL/localStorage/query string, ausencia de fluxo de direitos do titular, ausencia de inventario formal de tratamento/retencao/operadores e falta de controles de descarte/anonimizacao para dados persistentes.

Prioridade sugerida:

1. P0: corrigir transparencia publica, consentimento de cookies/analytics e contradicoes de retencao.
2. P0: concluir migracao para sessao segura sem `user_token` em URL/localStorage/query e sem token admin em query/localStorage.
3. P1: implementar canal e workflow de direitos do titular com registro, SLA e delecao/anonimizacao tecnica.
4. P1: formalizar inventario de dados, bases legais, operadores, retencao e RIPD interno.
5. P1/P2: reforcar seguranca: CSP, limpeza ativa de expirados, mascaramento em admin, restricao de anexos, auditoria de logs e minimizacao de metadados.

## Referencias LGPD/ANPD usadas

- A ANPD descreve direitos do titular como informacao, confirmacao/acesso, correcao, bloqueio/exclusao/portabilidade, eliminacao, revogacao do consentimento, informacoes sobre compartilhamento e revisao de decisoes automatizadas: https://www.gov.br/anpd/pt-br/assuntos/titular-de-dados-1/direito-dos-titulares
- A ANPD recomenda RIPD quando o tratamento puder gerar alto risco e indica que o relatorio deve descrever tipos de dados, operacoes, finalidades, hipoteses legais, riscos e medidas de mitigacao: https://www.gov.br/anpd/pt-br/canais_atendimento/agente-de-tratamento/relatorio-de-impacto-a-protecao-de-dados-pessoais-ripd
- A ANPD informa servicos para titulares e agentes, incluindo denuncia/peticao, comunicacao de incidente de seguranca, encarregado e transferencia internacional: https://www.gov.br/anpd

## Mapa de dados observado

### Upload, conversao e conciliacao

- Entradas: PDF/CSV/XLSX/OFX em `/analyze`, `/convert` e `/reconcile`.
- Dados provaveis: nome/razao social, CPF/CNPJ, agencia/conta, saldo, transacoes, descricoes de pagamentos, contrapartes, datas e valores.
- Persistencia: `TempAnalysisStorage` salva `analysis.json`, `report.xlsx`, `converted.ofx`, `converted.csv`, `converted.xlsx`, `reconcile.json` e relatorios de conciliacao sob `backend/tmp/analysis` por `ANALYSIS_TTL_SECONDS`, padrao de 86400 segundos.
- Evidencia: `backend/app/dependencies.py`, `backend/app/application/storage_service.py`, `backend/app/routers/analyze.py`, `backend/app/routers/convert.py`, `backend/app/routers/reconcile.py`.

### Conta, autenticacao e sessoes

- Entradas: nome, e-mail, senha, Google OAuth, tokens de usuario, cookies de sessao.
- Persistencia: tabela `users`, `google_oauth_states`, `user_sessions`; refresh token hashado, IP e user-agent de sessao.
- Risco principal: fluxo legado ainda retorna `user_token`, aceita `user_token` em query/body e grava token em `localStorage`/cookie acessivel por JavaScript.
- Evidencia: `backend/app/routers/auth.py`, `backend/app/routers/auth_session.py`, `backend/app/application/google_oauth_service.py`, `frontend/login.js`, `frontend/signup.js`, `frontend/auth-callback.js`, `doc/plan-token-security-strong.md`.

### Checkout e area admin

- Entradas: nome, e-mail, WhatsApp, CPF/CNPJ, observacoes, plano, link de pagamento.
- Persistencia: `checkout_intents`, `checkout_intent_events`, `user_plan_subscriptions`.
- Risco principal: CPF/CNPJ e WhatsApp ficam persistidos sem politica de retencao/apagamento clara; admin lista dados pessoais completos; admin token e aceito via query e armazenado em `localStorage`.
- Evidencia: `backend/app/routers/checkout.py`, `backend/app/application/checkout_management.py`, `backend/app/application/access_control_schema_sqlite_legacy.py`, `frontend/admin.js`, `frontend/checkout.html`.

### Contato e terceiros

- Entradas: nome, e-mail, assunto, mensagem e anexo.
- Compartilhamento: envio para Resend quando configurado; GTM em varias paginas; Google OAuth; Google Fonts; possivel provedor de infraestrutura/banco.
- Riscos principais: politica nao lista operadores/suboperadores com finalidade; anexo de contato nao tem allowlist de tipo; GTM carrega antes de consentimento.
- Evidencia: `backend/app/routers/contact.py`, `backend/app/application/contact_service.py`, multiplos HTMLs com `googletagmanager.com`.

## Achados e backlog de implementacao

### P0-1 Corrigir politica de privacidade e copy contraditoria

Problema: a landing afirma "Seus dados nao sao armazenados" / "Nada fica salvo", mas o backend salva artefatos temporarios e metadados de historico/checkout. A politica existente e generica para o nivel de tratamento observado e nao detalha suficientemente categorias, operadores, retencao e direitos operacionais. Isso compromete transparencia, livre acesso e prestacao de contas.

Implementar:

- Manter a varredura de encoding/acentos como gate de entrega para paginas publicas.
- Trocar promessas absolutas por texto exato, por exemplo: "Arquivos de conversao sao mantidos temporariamente para gerar e baixar resultados e depois expiram; dados de conta, checkout e suporte podem ser mantidos conforme finalidade e obrigacoes legais."
- Atualizar politica com controlador, canal de privacidade, categorias de dados, finalidades, bases legais, prazo de retencao por categoria, operadores/terceiros, transferencia internacional quando aplicavel, direitos do titular, cookies/analytics e incidentes.
- Criar tabela publica simples de retencao.

Criterios de aceite:

- `scripts/lint_frontend_text.py` e varredura de mojibake sem ocorrencias de sequencias UTF-8 quebradas comuns em `frontend/**/*.html`, `frontend/**/*.js`, `frontend/**/*.css`.
- Nenhuma pagina afirma que "nada fica salvo" sem qualificar retencao temporaria e dados persistentes.
- Politica menciona explicitamente GTM/Google, Resend, provedor de hospedagem/banco e autenticacao Google, se usados em producao.

### P0-2 Consentimento real para cookies/analytics antes do GTM

Problema: GTM carrega no `<head>` de paginas publicas antes de qualquer escolha do titular. Para analytics/marketing, a base legal mais defensavel costuma exigir consentimento ou, no minimo, avaliacao documentada de legitimo interesse com opt-out efetivo. Hoje nao ha banner, preferencias nem bloqueio previo.

Implementar:

- Criar `frontend/privacy-consent.js` e UI de preferencias com categorias: necessarios, analytics, marketing.
- Carregar GTM somente apos consentimento de analytics/marketing; manter funcionalidades essenciais sem GTM.
- Persistir preferencia com versao da politica e timestamp; disponibilizar link "Preferencias de privacidade" no footer.
- Adicionar testes/lint para garantir que HTMLs nao injetam GTM diretamente.

Criterios de aceite:

- Ao abrir site sem consentimento, nenhuma requisicao para `googletagmanager.com` e disparada.
- Usuario consegue aceitar, recusar e alterar preferencias.
- Politica explica cookies/analytics e como revogar.

### P0-3 Remover tokens sensiveis de URL, query string e localStorage

Problema: o projeto ja tem plano de migracao, mas ainda ha `user_token` em respostas JSON, callback Google com `user_token` na URL, aceitacao de token por query/body e gravacao em `localStorage`. Admin tambem retorna `admin_token`, aceita `admin_token` em query e o frontend salva em `localStorage`. Tokens em URL/localStorage aumentam risco de vazamento por historico, extensoes, XSS, logs e referer.

Implementar:

- Finalizar `doc/plan-token-security-strong.md`.
- Fazer Google OAuth criar sessao no backend e redirecionar sem token na URL.
- Remover `user_token` de novos fluxos autenticados; manter compatibilidade temporaria com deprecacao, telemetria e data de corte.
- Migrar admin para sessao `HttpOnly` separada ou mesma sessao com role admin; remover `admin_token` por query/localStorage.
- Para endpoints autenticados por cookie, adicionar protecao CSRF.

Criterios de aceite:

- Nenhum token sensivel aparece em URL, query string, localStorage ou payload de sucesso dos fluxos novos.
- Testes cobrem login, Google OAuth, admin, logout, refresh, rotacao/reuse e CSRF.
- Rotas legadas aceitam token apenas durante janela documentada e com warning/metricas.

### P1-1 Criar fluxo de direitos do titular

Problema: nao ha endpoint/processo para confirmacao, acesso, correcao, exclusao, anonimização, revogacao de consentimento ou informacao sobre compartilhamento. O contato generico nao basta para demonstrar governanca.

Implementar:

- Criar pagina/canal `privacidade.html` ou secao na politica com e-mail dedicado/encarregado ou responsavel.
- Criar tabela `privacy_requests` com tipo de pedido, identificador, status, datas, evidencias e conclusao.
- Criar rotinas para exportar dados do usuario: conta, historico de conversoes, checkout, sessoes, consentimentos e tickets de contato quando vinculaveis.
- Criar rotina de exclusao/anonimizacao: revogar sessoes, remover/anonimizar historico de conversoes, checkout quando possivel, fingerprints anonimos e artefatos temporarios.
- Documentar excecoes legais/contratuais para retencao, especialmente dados fiscais/contratuais se existirem.

Criterios de aceite:

- Admin consegue registrar e concluir solicitacoes com trilha de auditoria.
- Usuario encontra canal claro sem depender de login.
- Testes cobrem exportacao e delecao/anonimizacao de usuario.

### P1-2 Politica tecnica de retencao e descarte

Problema: artefatos temporarios expiram quando acessados/listados, mas nao ha evidencia de limpeza ativa. Dados persistentes (`users`, `user_conversions`, `checkout_intents`, `sessions`, eventos admin) nao tem TTL, classificacao nem rotina de descarte.

Implementar:

- Criar `doc/data-retention-policy.md` com prazo por categoria.
- Criar job/endpoint administrativo seguro para purge de analises expiradas, sessoes expiradas, estados OAuth vencidos e historicos conforme prazo.
- Em `user_conversions`, separar dado necessario para produto de dado identificavel: avaliar truncar/hashear nome de arquivo apos expirar o artefato.
- Em `checkout_intents`, definir prazo para CPF/CNPJ/WhatsApp/observacoes; apos prazo, mascarar ou anonimizar campos nao obrigatorios.

Criterios de aceite:

- Comando de manutencao roda idempotente e tem testes.
- Metricas/logs informam quantidade de registros apagados/anonimizados sem expor conteudo.
- Politica publica e interna usam os mesmos prazos.

### P1-3 Inventario de tratamento e bases legais

Problema: nao existe registro formal das operacoes de tratamento, bases legais e compartilhamentos. A ANPD recomenda mapeamento, registro das operacoes, politicas/processos internos e canal com titulares.

Implementar:

- Criar `doc/lgpd-data-inventory.md` com: categoria de dado, origem, finalidade, base legal sugerida, endpoint/tabela/arquivo, operador, transferencia internacional, prazo, controles, dono interno.
- Separar bases legais por finalidade:
  - execucao de contrato/procedimentos preliminares: conversao solicitada, conta, checkout;
  - cumprimento legal/regulatorio: registros obrigatorios se aplicavel;
  - legitimo interesse: seguranca, prevencao a abuso, antifraude, logs minimos, desde que documentado;
  - consentimento: analytics/marketing/cookies nao essenciais;
  - exercicio regular de direitos: registros de solicitacoes e auditoria.
- Revisar se extratos contem dados sensiveis por contexto; em geral dados financeiros nao sao automaticamente "sensiveis" na LGPD, mas podem revelar informacoes sensiveis indiretamente.

Criterios de aceite:

- Inventario referencia rotas/tabelas reais.
- Toda coleta publica tem finalidade e base legal refletidas na politica.

### P1-4 RIPD interno para conversao/conciliacao financeira

Problema: o produto processa extratos e pode escalar para muitos titulares/empresas. Ha uso de parsing automatizado e perfilamento operacional de transacoes, ainda que nao pareca haver decisao automatizada com efeito juridico. Pela natureza financeira e volume potencial, recomenda-se RIPD interno.

Implementar:

- Criar `doc/ripd-conversao-conciliacao.md`.
- Incluir descricao de processos, tipos de dados, fluxo de upload, armazenamento temporario, historico persistente, terceiros, medidas de seguranca, riscos, probabilidade/impacto, mitigacoes e riscos residuais.
- Manter versao publica resumida sem expor detalhes sensiveis.

Criterios de aceite:

- RIPD cobre conversao, conciliacao, checkout, autenticacao e suporte.
- Cada risco alto tem acao vinculada ao backlog.

### P1-5 Reforcar seguranca de frontend/API

Problema: existem headers basicos, mas falta CSP. O uso de GTM/scripts inline aumenta superficie de XSS; tokens legados em JS tornam XSS mais grave.

Implementar:

- Adicionar `Content-Security-Policy` progressiva. Inicialmente `Report-Only`; depois enforcement.
- Remover scripts inline de GTM quando consent manager estiver pronto.
- Adicionar `Permissions-Policy`.
- Avaliar `Cache-Control: no-store` para respostas autenticadas e downloads sensiveis.
- Garantir que downloads de relatorio exijam dono quando houver identidade e que links/IDs nao sejam enumeraveis.

Criterios de aceite:

- Testes verificam headers em rotas estaticas, autenticadas e downloads.
- CSP nao quebra fontes/scripts autorizados e bloqueia origens nao esperadas.

### P1-6 Minimizar exposicao no admin

Problema: admin lista e busca por e-mail, nome, WhatsApp, CPF/CNPJ, observacoes e historico. Isso e necessario para operacao, mas deve ser minimizado e auditavel.

Implementar:

- Mascarar CPF/CNPJ, WhatsApp e e-mail por padrao; revelar sob acao explicita com log.
- Adicionar auditoria de visualizacao/exportacao de dados pessoais no admin.
- Remover query param `admin_token`.
- Aplicar menor privilegio para funcoes admin: pedidos, usuarios, papeis.

Criterios de aceite:

- Dados sensiveis nao aparecem completos na listagem inicial.
- Eventos de acesso a detalhes ficam registrados com ator, horario e finalidade.

### P2-1 Validar anexos de contato

Problema: contato aceita anexo ate 2 MB e envia ao Resend sem allowlist de tipo/extensao ou varredura. O risco e operacional e de seguranca.

Implementar:

- Permitir apenas tipos necessarios (`pdf`, `png`, `jpg`, `csv`, `xlsx`) ou remover anexos no MVP.
- Validar MIME/extensao, tamanho e nome seguro.
- Avisar na UI para nao enviar dados sensiveis desnecessarios.

Criterios de aceite:

- Testes cobrem arquivo permitido, proibido e acima do limite.
- Politica menciona anexos de suporte e destino.

### P2-2 Melhorar anonimato/fingerprint

Problema: fingerprints anonimos em `localStorage` podem ser dados pessoais/pseudonimos. Hoje sao tratados como mecanismo tecnico, mas precisam aparecer no inventario/politica.

Implementar:

- Documentar finalidade de quota/abuso.
- Definir prazo de retencao e reset.
- Oferecer explicacao na politica de cookies/armazenamento local.

Criterios de aceite:

- Fingerprint consta no inventario.
- Usuario sabe como limpar/revogar preferencias locais.

## Ajustes especificos de texto publico

Substituir:

- "Seus dados nao sao armazenados"
- "Nada fica salvo"
- "Os arquivos sao descartados apos a conversao"

Por uma versao verificavel:

> Usamos os arquivos enviados apenas para processar a conversao ou conciliacao solicitada. Os artefatos tecnicos ficam disponiveis temporariamente para revisao e download e expiram conforme nossa politica de retencao. Dados de conta, checkout e suporte podem ser mantidos enquanto forem necessarios para prestar o servico, cumprir obrigacoes legais e proteger a plataforma.

## Validacao recomendada para a PR de implementacao

- `backend\\venv\\Scripts\\python.exe -m pytest backend/tests -q --basetemp C:\\Users\\erica\\AppData\\Local\\Temp\\gettdone-pytest-lgpd`
- `backend\\venv\\Scripts\\python.exe -m ruff check backend`
- `python scripts/lint_frontend_text.py`
- Varredura de tokens legados e GTM direto: `rg -n "user_token|admin_token|googletagmanager" frontend backend/app doc -S`
- Playwright/smoke manual:
  - abrir home sem consentimento e confirmar ausencia de GTM;
  - aceitar analytics e confirmar carregamento;
  - recusar analytics e confirmar persistencia da recusa;
  - login normal sem token em URL/localStorage;
  - Google OAuth sem `user_token` no callback;
  - admin sem token em localStorage/query;
  - upload/conversao/download ainda funcionando;
  - pedido de exclusao/anonimizacao em ambiente local.

## Ordem sugerida de PRs

1. `fix(frontend): corrigir transparencia LGPD e mojibake`
2. `feat(frontend): adicionar consentimento de cookies e bloquear GTM por padrao`
3. `feat(auth): concluir sessoes HttpOnly e remover tokens legados dos fluxos novos`
4. `feat(privacy): adicionar workflow de direitos do titular`
5. `feat(data): adicionar politica e jobs de retencao/descarte`
6. `docs(lgpd): adicionar inventario de dados e RIPD`
7. `feat(security): adicionar CSP e auditoria admin de dados pessoais`

## Risco residual apos implementacao

Mesmo com as tarefas acima, a conformidade depende de configuracao real de producao: contratos com operadores, DPA, localidade/transferencia internacional, logs do provedor, backups, processo de incidente, nomeacao/canal do encarregado ou responsavel, e revisao juridica das bases legais. O codigo pode sustentar a conformidade, mas nao substitui a governanca operacional.

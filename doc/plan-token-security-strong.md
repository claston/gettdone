# Plano de Hardening de Token (Nível Forte)

## Objetivo

Migrar autenticação de frontend para sessão segura com cookie `HttpOnly`, removendo dependência de token acessível por JavaScript (`localStorage`/`document.cookie`), com rotação, revogação e proteção CSRF.

## Estado Atual (resumo técnico)

- `POST /auth/login` e `POST /auth/register` retornam `user_token` no JSON.
- `GET /auth/me` aceita `Bearer` e também `user_token` por query.
- Google OAuth redireciona para `auth-callback.html` com `user_token` na URL.
- Frontend ainda usa `USER_TOKEN_KEY`/`USER_TOKEN_COOKIE` em múltiplas páginas.
- Token atual é assinado por HMAC em `AccessControlService._encode_token()` e não possui rotação/revogação por sessão.

## Estratégia de Rollout

Implementar em dual-stack por etapas curtas: novo fluxo por cookie primeiro, legado por bearer temporariamente mantido, corte final somente após validação.

## PR-1: Base de Sessão Segura (Cookie HttpOnly + Refresh + Logout)

Escopo:
- Criar modelo de sessão no backend com refresh token rotativo (hashado em banco).
- Adicionar endpoints:
- `POST /auth/session/login` (seta cookies, não retorna token sensível)
- `POST /auth/session/refresh` (rotação de refresh)
- `POST /auth/session/logout` (revoga sessão atual e expira cookies)
- `POST /auth/session/logout-all` (revoga todas as sessões do usuário)

Arquivos alvo:
- `backend/app/application/access_control.py`
- `backend/app/routers/auth.py`
- `backend/app/schemas.py`
- `backend/app/dependencies.py`
- `backend/app/main.py` (cookies e headers auxiliares)
- `backend/alembic/versions/*` (migração aditiva)
- `backend/tests/test_auth_api.py`
- `backend/tests/test_google_auth_api.py`

Persistência (aditiva):
- Nova tabela `user_sessions` com:
- `id`, `user_id`, `refresh_token_hash`, `refresh_token_family`, `created_at`, `expires_at`, `rotated_at`, `revoked_at`, `last_ip`, `last_user_agent`.
- Índices por `user_id`, `refresh_token_hash`, `expires_at`.
- Sem remoção de colunas existentes.

Cookies propostos:
- `__Host-ofx_at`: access curto (10-15 min), `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`.
- `__Host-ofx_rt`: refresh (7-14 dias), `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/auth/session/refresh`.

Critérios de aceite:
- Login cria cookies seguros e `auth/me` funciona com `credentials=include`.
- Refresh rotaciona token antigo e invalida reuse.
- Logout expira cookies e invalida sessão.

## PR-2: Migração dos Endpoints de Produto para Sessão por Cookie

Escopo:
- Permitir identidade do usuário via cookie de sessão nos endpoints de produto.
- Tornar `user_token` opcional/deprecated em payload/query nos endpoints que hoje exigem token explícito.

Arquivos alvo:
- `backend/app/routers/convert.py`
- `backend/app/routers/report.py`
- `backend/app/routers/client.py`
- `backend/app/routers/checkout.py`
- `backend/app/routers/reconcile.py`
- `backend/tests/test_convert_api.py`
- `backend/tests/test_convert_report_api.py`
- `backend/tests/test_client_conversions_api.py`
- `backend/tests/test_checkout_api.py`
- `backend/tests/test_reconcile_api.py`

Critérios de aceite:
- Fluxos autenticados funcionam sem enviar `user_token` no body/query.
- Compatibilidade temporária com bearer legado mantida.
- Mensagens de erro distinguem ausência de sessão vs sessão inválida.

## PR-3: OAuth sem Token em URL + Callback Seguro

Escopo:
- No callback Google, backend deve setar sessão em cookie e redirecionar sem `user_token` em query.
- `frontend/auth-callback.js` passa a apenas ler status/next (sem gravar token sensível).

Arquivos alvo:
- `backend/app/application/google_oauth_service.py`
- `backend/app/routers/auth.py`
- `frontend/auth-callback.js`
- `frontend/login.js`
- `frontend/signup.js`
- `backend/tests/test_google_auth_api.py`

Critérios de aceite:
- Nenhum token sensível aparece em URL, logs de acesso ou histórico do browser.
- Login Google mantém redirecionamento para `next` com sessão ativa.

## PR-4: CSRF + Headers + CSP Forte

Escopo:
- CSRF para `POST/PUT/PATCH/DELETE` quando autenticado por cookie.
- Política de segurança de conteúdo (CSP) estrita com nonce.
- Endurecimento de headers para reduzir impacto de XSS e clickjacking.

Arquivos alvo:
- `backend/app/main.py`
- `backend/app/security_baseline.py`
- `backend/tests/test_main_security_headers.py`
- Frontend HTML principal para adoção de nonce onde necessário.

Controles mínimos:
- `Content-Security-Policy` sem `unsafe-inline` e sem `unsafe-eval`.
- `X-Frame-Options: DENY` (já existe, manter).
- `Referrer-Policy`, `X-Content-Type-Options`, HSTS (já existe, reforçar).
- `Cache-Control: no-store` em respostas de auth/sessão.

Critérios de aceite:
- Requisições mutáveis sem CSRF válido são bloqueadas.
- Scripts inline sem nonce deixam de executar.

## PR-5: Corte do Legado

Escopo:
- Remover leitura de `USER_TOKEN_KEY`/`USER_TOKEN_COOKIE` das páginas de produto.
- Descontinuar `user_token` por query/body em rotas de usuário autenticado.
- Manter `anonymous_fingerprint` somente para fluxo anônimo.

Arquivos alvo:
- `frontend/ofx-convert.js`
- `frontend/client-area.js`
- `frontend/checkout.js`
- `frontend/contato.js`
- `frontend/index.js`
- `backend/app/routers/auth.py` e demais rotas com fallback legado
- Testes de integração relacionados

Critérios de corte:
- Telemetria indicando adoção do fluxo de cookie > 95%.
- Janela de transição comunicada e concluída.
- Sem regressão em login, conversão e download de relatório.

## Testes Obrigatórios por PR

- Lint frontend:
- `backend\venv\Scripts\python.exe scripts\lint_frontend_text.py`
- `backend\venv\Scripts\python.exe scripts\lint_frontend_navigation.py`
- Backend:
- `backend\venv\Scripts\python.exe -m pytest backend/tests -q --basetemp <temp_unico>`
- Validação manual de API (mínimo V1):
- `POST /analyze` happy path
- `GET /report/{analysis_id}` happy path
- 1 caso negativo (`invalid file` ou `analysis_id` inexistente)

## Critério Final de Segurança Forte (Definition of Done)

- Nenhum token de sessão acessível via JavaScript.
- Nenhum token de sessão em URL.
- Access token curto + refresh rotativo com detecção de reuse.
- Revogação de sessão atual e logout global funcionando.
- CSRF ativo para mutações.
- CSP forte ativa em produção.
- Compatibilidade de rollout comprovada e sem mudança destrutiva de schema.

## Riscos e Mitigações

- Risco: quebra de sessão em subdomínio.
- Mitigação: dual-stack + rollout gradual + canário.
- Risco: impacto em fluxos antigos com query token.
- Mitigação: depreciação em fases com logs de uso.
- Risco: complexidade de CSRF com SPA.
- Mitigação: implementar primeiro em endpoints críticos, depois expandir.

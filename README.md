# gettdone

Fundacao inicial do projeto com:

- `backend/` em FastAPI (padrao inspirado no `system-context`)
- `frontend/` estatico (`HTML + JavaScript`)
- docs de especificacao e backlog em `doc/`

## Estrutura

```text
backend/
  app/
    application/
    routers/
    dependencies.py
    main.py
    schemas.py
  tests/
frontend/
  index.html
  app.js
  styles.css
doc/
```

## Rodar backend

```powershell
cd backend
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn app.main:app --reload --env-file .env
```

API docs: `http://127.0.0.1:8000/docs`

TTL de analises (opcional):

```powershell
$env:ANALYSIS_TTL_SECONDS = "86400" # 24 horas
```

Proteção de capacidade da conversão inline:

- `CONVERSION_MAX_CONCURRENCY=1` limita o processo a uma conversão ativa por vez; são aceitos valores de `1` a `4`.
- Aumentar o tamanho da máquina não altera esse valor automaticamente. Ajuste a variável gradualmente e reinicie o serviço após observar memória, CPU, duração e rejeições `conversion_capacity_rejected` nos logs.
- `CONVERSION_BUSY_RETRY_AFTER_SECONDS=15` define a espera sugerida quando a capacidade está ocupada.
- Quando todas as vagas estão em uso, os endpoints de conversão retornam `503` com `Retry-After`, sem iniciar a conversão nem consumir cota.
- O limite é por processo da aplicação. A configuração atual do Render usa um processo Uvicorn, portanto ele também representa o limite da instância.

Identidade anônima por cookie:

  - `POST /auth/anonymous-session` cria um identificador aleatório no servidor e o entrega em cookie `HttpOnly`.
  - Abrir a página não cria uma linha no banco: a identidade só é persistida no primeiro uso de conversão ou conciliação.
- O frontend não cria nem persiste novos fingerprints anônimos no `localStorage`.
- Durante o rollout, um fingerprint legado que já exista no banco pode ser migrado uma vez pelo navegador. A mesma linha de `anonymous_identities` e o mesmo consumo em `usage` são preservados.
- O backend continua aceitando temporariamente `anonymous_fingerprint` nos contratos antigos para compatibilidade com páginas em cache. A remoção desse fallback deve ocorrer em um PR posterior, após observar a adoção do cookie.
- `ANONYMOUS_IDENTITY_COOKIE_TTL_SECONDS=2592000` define a validade do cookie; o mínimo aplicado é de sete dias para não reduzir a janela semanal da cota.
  - `ANONYMOUS_IDENTITY_COOKIE_SECURE=true` deve permanecer ativo em produção. O nome padrão é `__Host-ofx_anon` em produção e `ofx_anon` em desenvolvimento.
  - Esta etapa não coleta nem usa endereço IP. O identificador é pseudônimo, exclusivo da sessão anônima e assinado com `ACCESS_CONTROL_TOKEN_SECRET`.
  - Em deploys separados, publique o backend antes do frontend e mantenha `ACCESS_CONTROL_TOKEN_SECRET` estável; a rotação invalida os cookies anônimos existentes.
  - Depois que o frontend remover o identificador legado, um rollback deve preservar o endpoint e a leitura do cookie. Reverter todo o fluxo para `localStorage` faria esses navegadores receberem uma identidade nova.

Armazenamento compartilhado de documentos de conversão:

- `CONVERSION_DOCUMENT_STORE=filesystem` mantém o comportamento local atual e é o padrão.
- `CONVERSION_DOCUMENT_STORE=s3` usa o bucket privado definido em `CONVERSION_S3_BUCKET`.
- `CONVERSION_S3_PREFIX=conversion/jobs` separa os objetos temporários dentro do bucket.
- `CONVERSION_S3_SERVER_SIDE_ENCRYPTION=AES256` habilita criptografia explícita no envio. Para KMS, use `aws:kms` e informe `CONVERSION_S3_KMS_KEY_ID`.
- Essa configuração ainda não ativa processamento assíncrono: o fluxo continua inline, mas passa a armazenar, carregar e excluir o documento pelo S3. Isso permite validar o armazenamento antes da introdução de SQS e Lambda.

OCR para PDF sem camada de texto:

- Em `development`, o OCR pode ser autoativado quando houver Tesseract instalado e `backend/tmp/tessdata` com idiomas disponiveis.
- Em `production`, o OCR continua desligado por padrao; use `PDF_OCR_ENABLED=true` para habilitar explicitamente.
- Idioma padrao: `por+eng` (configuravel por `PDF_OCR_LANG`).
- Limite padrao de paginas no fallback: `12` (configuravel por `PDF_OCR_MAX_PAGES`).
- DPI padrao de renderizacao OCR: `250` (configuravel por `PDF_OCR_DPI`; faixa recomendada `150` a `400`).
- Limite padrao de tamanho para OCR: `5 MB` (configuravel por `PDF_OCR_MAX_FILE_MB`).
- Timeout padrao de OCR por pagina: `12s` (configuravel por `PDF_OCR_PAGE_TIMEOUT_SECONDS`).
- Concorrencia padrao de OCR por processo: `1` (configuravel por `PDF_OCR_CONCURRENCY_LIMIT`).

Textract para PDF escaneado:

- Ative com `TEXTRACT_ENABLED=true`.
- O modo padrao agora e `TEXTRACT_MODE=text`, que usa apenas extracao de texto (`StartDocumentTextDetection`).
- Use `TEXTRACT_MODE=analysis` somente quando quiser habilitar o caminho com tabelas/layout para pacotes futuros.
- Para o teste local, configure tambem `AWS_REGION` e `TEXTRACT_TEMP_BUCKET`.
- `TEXTRACT_FEATURE_TYPES` so e usado em `TEXTRACT_MODE=analysis`.
- Se quiser forcar Textract mesmo quando o PDF tiver texto nativo, use `TEXTRACT_FORCE=true`.
- A imagem Lambda usa Textract como caminho de OCR e nao inclui o binario Tesseract; o fallback local continua opcional no runtime legado do Render.

## Rodar frontend

```powershell
.\scripts\dev-frontend.ps1
```

App web: `http://localhost:3000`

## Rodar frontend e backend juntos (fluxo rapido)

Use dois terminais:

Terminal 1 (backend):

```powershell
cd backend
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
```

Terminal 2 (frontend):

```powershell
.\scripts\dev-frontend.ps1
```

Validacao minima:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
Invoke-WebRequest http://127.0.0.1:3000 | Select-Object -ExpandProperty StatusCode
```

Opcional: iniciar os dois em segundo plano no mesmo terminal:

```powershell
$py = "backend\venv\Scripts\python.exe"
$backend = Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory "backend" -PassThru
$frontend = Start-Process -FilePath $py -ArgumentList "-m","http.server","3000" -WorkingDirectory "frontend" -PassThru
"BACKEND_PID=$($backend.Id)"
"FRONTEND_PID=$($frontend.Id)"
```

Para parar quando terminar:

```powershell
Stop-Process -Id <BACKEND_PID>,<FRONTEND_PID> -Force
```

## Corrigir frontend

```powershell
.\scripts\fix-frontend.ps1
```

## Hook local de qualidade de texto (UTF-8 + acentuacao)

Para bloquear commit com mojibake/caracteres invalidos no frontend:

```powershell
.\scripts\install-git-hooks.ps1
```

Isso ativa o `pre-commit` versionado em `.githooks/pre-commit`, que executa:

```powershell
backend\venv\Scripts\python.exe scripts\lint_frontend_text.py
backend\venv\Scripts\python.exe scripts\lint_frontend_navigation.py
```

Smoke test de navegacao com Playwright (opcional local):

```powershell
backend\venv\Scripts\python.exe -m pip install playwright
backend\venv\Scripts\python.exe -m playwright install chromium
backend\venv\Scripts\python.exe scripts\smoke_playwright_navigation.py
```

## Testes de backend durante o desenvolvimento

Para executar a suíte completa em quatro processos, com um diretório temporário isolado:

```powershell
.\scripts\test-backend.ps1
```

O script aceita argumentos adicionais do pytest. Por exemplo, para rodar apenas um arquivo:

```powershell
.\scripts\test-backend.ps1 backend/tests/test_access_control.py
```

Defina `PYTEST_WORKERS` para alterar a quantidade de processos. O padrão validado localmente é `4`.

## CI no GitHub

Workflows configurados:

- `CI | Lint and Tests`: roda `ruff` e `pytest` do `backend` em push/PR.
- `CI | Lint and Tests` (`pdf-golden` job): roda `pytest -m pdf_golden` como guarda de regressao dedicada para parser PDF.
- `Security | CodeQL Scan`: roda analise de seguranca para Python em push/PR para `main` e agenda semanal.
- `CD | Publish Container (GHCR)`: publica imagem Docker no GHCR em push para `main`.
- `CD | Deploy to Render (Staging)`: dispara deploy no Render apos publish da imagem.

## Regressao PDF Golden (parser)

Para rodar apenas o pacote minimo de regressao do parser PDF:

```powershell
cd backend
venv\Scripts\python.exe -m pytest -m pdf_golden -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-pdf-golden
```

Opcao equivalente executando da raiz do repositorio:

```powershell
backend\venv\Scripts\python.exe -m pytest -m pdf_golden -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-pdf-golden
```

Arquivos principais desse pacote:

- `backend/tests/test_pdf_parser_golden_minimal_dataset.py`
- `backend/tests/fixtures/pdf_golden_samples.py`

Cobertura atual do starter pack (sintetico):

- `Nubank`
- `Itau`
- `Santander`
- `Bradesco`
- `Banco do Brasil`
- `Caixa`
- `Inter`
- `Sicredi`
- `Year rollover (dez/jan)` multi-page

Cobertura quasi-real adicionada (anonimizada):

- `inline_noise`: cabecalho, periodo, linhas de saldo e descricoes longas
- `multipage_inline`: transacoes distribuidas entre paginas com rastreabilidade de origem
- `signed_ambiguous`: sinal explicito no valor com descricao ambigua (`CREDITO`/`ESTORNO`)
- negativo `no_pattern`: extrato realista sem linhas transacionais reconheciveis

Contrato atual validado no pacote:

- parser selecionado por cenario
- contagens/campos canônicos de parse metrics
- `first_transaction` e `last_transaction` (data, valor, tipo e descricao quando aplicavel)
- rastreabilidade de origem canônica (`source_page`, `source_line`)
- cenarios multi-page sinteticos
- gate de qualidade textual para evitar mojibake nos samples

Checklist rapido de validacao local (fase atual):

1. `cd backend`
2. `venv\Scripts\python.exe -m pytest -m pdf_golden -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-pdf-golden`
3. `venv\Scripts\python.exe -m pytest tests/test_pdf_parser.py tests/test_pdf_parser_golden_minimal_dataset.py -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-parser-golden`
4. `venv\Scripts\python.exe -m pytest tests/test_analyze_report_http_real_api.py -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-api-e2e-minimum`

## Deploy no Render (Web Service)

Este repositorio agora suporta deploy no Render via imagem Docker.

Arquivos de deploy:

- `Dockerfile` (na raiz): sobe API FastAPI e frontend estatico no mesmo servico.
- `.dockerignore`: reduz contexto de build.
- `.github/workflows/publish-ghcr.yml`: publica imagem em `ghcr.io/<owner>/gettdone`.
- `.github/workflows/deploy-render-staging.yml`: faz trigger de deploy pela API do Render.
- `.github/workflows/deploy-worker-ecr.yml`: publica o worker no ECR e atualiza a Lambda por digest usando GitHub OIDC.

Passo a passo no Render:

1. Crie um `Web Service` no Render e aponte para a imagem `ghcr.io/<owner>/gettdone:staging`.
2. Configure `Health Check Path` como `/health`.
3. Defina `PORT` (Render injeta automaticamente; o container ja respeita esse valor).
4. (Opcional) Defina `CORS_ALLOW_ORIGINS` com dominios permitidos separados por virgula.

## Deploy do worker AWS

O workflow `CD | Deploy Worker Image (ECR)` é manual e aceita deploy somente da
branch `main`. Configure a variável de Actions `AWS_WORKER_DEPLOY_ROLE_ARN` com
o output homônimo do bootstrap de infraestrutura. O workflow usa credenciais
temporárias OIDC, constrói `Dockerfile.lambda` para `linux/amd64`, publica uma
tag imutável por commit e atualiza somente `ConversionImageUri` na stack do
worker. Nenhuma access key AWS, URL do banco ou token da aplicação é armazenado
no GitHub.

A imagem do worker possui uma fronteira própria: compartilha o core de
conversão, mas exclui frontend, rotas, administração, autenticação, checkout e
dependências web. Enquanto a publicação permanecer operacionalmente manual,
siga o runbook de [isolamento e publicação manual do worker](docs/worker-runtime-isolation.md).

## Baseline de seguranca (Fase 0 - MVP)

Quando `APP_ENV=production`, a aplicacao agora faz validacao de seguranca na inicializacao e nao sobe se houver configuracao insegura.

Variaveis obrigatorias em producao:

- `APP_ENV=production`
- `ACCESS_CONTROL_TOKEN_SECRET` com no minimo 32 caracteres e diferente do valor de desenvolvimento
- `CORS_ALLOW_ORIGINS` com dominio(s) real(is) da aplicacao (sem localhost)
- `ENABLE_API_DOCS=false`
- `UNLIMITED_ANON_QUOTA=false`

Checklist rapido para Render:

1. Definir `APP_ENV=production`.
2. Definir `ACCESS_CONTROL_TOKEN_SECRET` forte (32+ chars aleatorios).
3. Definir `CORS_ALLOW_ORIGINS` com origem exata (ex.: `https://seu-dominio.com`).
4. Definir `ENABLE_API_DOCS=false`.
5. Definir `UNLIMITED_ANON_QUOTA=false`.
6. Fazer deploy e validar `GET /health`.
7. (Opcional para Neon/Postgres) Definir `DATABASE_URL` no formato `postgresql+psycopg://...`.
8. (Recomendado com banco compartilhado) Definir `DATABASE_SCHEMA` exclusivo para esta app (ex.: `gettdone`).

Para desenvolvimento local, continue usando:

- `APP_ENV=development` (ou sem definir `APP_ENV`)
- docs habilitadas por padrao
- CORS com `localhost:3000` e `127.0.0.1:3000`

### Formulário de contato via Hostinger

O formulário pode usar o SMTP da Hostinger sem alterar o Resend usado pelos e-mails transacionais. Para ativar
o fluxo em produção, configure no backend:

- `CONTACT_DELIVERY_PROVIDER=hostinger_smtp`
- `CONTACT_SMTP_HOST=smtp.hostinger.com`
- `CONTACT_SMTP_PORT=465`
- `CONTACT_SMTP_USE_SSL=true`
- `CONTACT_SMTP_USERNAME=contato@ofxsimples.com.br`
- `CONTACT_SMTP_PASSWORD` com a senha da caixa de e-mail (secret)
- `CONTACT_SMTP_FROM_EMAIL=contato@ofxsimples.com.br`
- `CONTACT_TO_EMAIL=contato@ofxsimples.com.br`
- `CONTACT_SMTP_DRY_RUN=false`

O remetente e o destinatário ficam na Hostinger, enquanto o endereço informado pelo visitante é definido como
`Reply-To`. Para rollback sem mudança de código, restaure `CONTACT_DELIVERY_PROVIDER=resend`.

### Verificação de e-mail para contas locais

A verificação permanece desligada por padrão para permitir rollout compatível. Antes de ativá-la em produção,
configure o Resend e valide o envio para um endereço controlado:

- `AUTH_EMAIL_VERIFICATION_REQUIRED=false` durante o primeiro deploy; altere para `true` após o smoke test
- `AUTH_EMAIL_VERIFICATION_TTL_SECONDS=3600`
- `AUTH_EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS=60`
- `AUTH_EMAIL_VERIFICATION_DAILY_LIMIT=5`
- `AUTH_EMAIL_VERIFICATION_FRONTEND_URL=https://www.ofxsimples.com.br/verificar-email.html`
- `RESEND_API_KEY` com uma chave válida
- `CONTACT_FROM_EMAIL` usando um remetente verificado
- `CONTACT_RESEND_DRY_RUN=false`

Com a flag ativa, novos cadastros por senha ficam pendentes até a confirmação. Usuários existentes e identidades
Google verificadas permanecem liberados. Para rollback imediato, defina `AUTH_EMAIL_VERIFICATION_REQUIRED=false`;
a migração é aditiva e não precisa ser revertida.

Secrets/vars recomendados no GitHub (environment `staging`):

- `RENDER_API_KEY` (secret)
- `RENDER_STAGING_SERVICE_ID` (secret)
- `RENDER_DEPLOY_ENABLED=true` (variable, opcional)
- `DATABASE_URL` (secret, para job de migracao Alembic)
- `DATABASE_SCHEMA` (variable, opcional; default `public`)

## Migrations de banco (Alembic)

Quando `DATABASE_URL` estiver definido, a esteira de deploy para staging executa:

- `python -m alembic upgrade head` (diretorio `backend/`)

Execucao local:

```powershell
cd backend
$env:DATABASE_URL = "postgresql+psycopg://user:pass@host:5432/dbname"
$env:DATABASE_SCHEMA = "gettdone"
venv\Scripts\python.exe -m alembic upgrade head
```

Fluxo legado (banco existente sem `alembic_version`):

```powershell
cd backend
$env:DATABASE_URL = "postgresql+psycopg://user:pass@host:5432/dbname"
$env:DATABASE_SCHEMA = "gettdone"
venv\Scripts\python.exe -m alembic stamp 20260508_01
venv\Scripts\python.exe -m alembic upgrade head
```

Rollback local (ultimo passo):

```powershell
cd backend
venv\Scripts\python.exe -m alembic downgrade -1
```

Comportamento de frontend em deploy:

- Em producao, o frontend chama a API no mesmo dominio do servico.
- Em desenvolvimento local em `localhost:3000`, continua usando `http://127.0.0.1:8000`.

## Endpoints fundacao

- `GET /health`
- `POST /analyze` (stub funcional)
- `GET /report/{analysis_id}` (stub funcional com XLSX)

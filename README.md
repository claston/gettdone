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
venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

API docs: `http://127.0.0.1:8000/docs`

TTL de analises (opcional):

```powershell
$env:ANALYSIS_TTL_SECONDS = "86400" # 24 horas
```

OCR para PDF sem camada de texto:

- OCR esta desativado nesta fase para evitar risco de consumo excessivo de memoria/CPU em ambientes de usuario.
- O fluxo atual aceita PDFs com texto extraivel e retorna erro orientativo para PDFs escaneados/imagem.
- O codigo experimental de OCR fica isolado para hardening futuro, mas nao e chamado pelo parser em runtime.

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
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
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

Opcao equivalente executando da raiz do repositorio (sem warning de marker):

```powershell
backend\venv\Scripts\python.exe -m pytest -c backend\pyproject.toml -m pdf_golden -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-pdf-golden
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

## Deploy no Render (Web Service)

Este repositorio agora suporta deploy no Render via imagem Docker.

Arquivos de deploy:

- `Dockerfile` (na raiz): sobe API FastAPI e frontend estatico no mesmo servico.
- `.dockerignore`: reduz contexto de build.
- `.github/workflows/publish-ghcr.yml`: publica imagem em `ghcr.io/<owner>/gettdone`.
- `.github/workflows/deploy-render-staging.yml`: faz trigger de deploy pela API do Render.

Passo a passo no Render:

1. Crie um `Web Service` no Render e aponte para a imagem `ghcr.io/<owner>/gettdone:staging`.
2. Configure `Health Check Path` como `/health`.
3. Defina `PORT` (Render injeta automaticamente; o container ja respeita esse valor).
4. (Opcional) Defina `CORS_ALLOW_ORIGINS` com dominios permitidos separados por virgula.

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

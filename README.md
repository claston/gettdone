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

## CI no GitHub

Workflows configurados:

- `CI | Lint and Tests`: roda `ruff` e `pytest` do `backend` em push/PR.
- `Security | CodeQL Scan`: roda analise de seguranca para Python em push/PR para `main` e agenda semanal.
- `CD | Publish Container (GHCR)`: publica imagem Docker no GHCR em push para `main`.
- `CD | Deploy to Render (Staging)`: dispara deploy no Render apos publish da imagem.

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

Secrets/vars recomendados no GitHub (environment `staging`):

- `RENDER_API_KEY` (secret)
- `RENDER_STAGING_SERVICE_ID` (secret)
- `RENDER_DEPLOY_ENABLED=true` (variable, opcional)

Comportamento de frontend em deploy:

- Em producao, o frontend chama a API no mesmo dominio do servico.
- Em desenvolvimento local em `localhost:3000`, continua usando `http://127.0.0.1:8000`.

## Endpoints fundacao

- `GET /health`
- `POST /analyze` (stub funcional)
- `GET /report/{analysis_id}` (stub funcional com XLSX)

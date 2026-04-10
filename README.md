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

## Endpoints fundacao

- `GET /health`
- `POST /analyze` (stub funcional)
- `GET /report/{analysis_id}` (stub funcional com XLSX)

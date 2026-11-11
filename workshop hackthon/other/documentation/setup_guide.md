# Setup Guide (Windows)

Two ways to run the project. All commands are Windows PowerShell and assume you start in
the project root folder `workshop hackthon`.

| | Option A - Docker | Option B - manual |
|---|---|---|
| Needs | Docker Desktop | Python 3.12, Node.js 20+ (22 tested), PostgreSQL 18 (native or Docker) |
| Frontend | http://localhost:8080 | http://localhost:5173 (dev) or :4173 (preview) |
| API | http://localhost:8000/docs | http://localhost:8000/docs |

---

## Option A - Docker Compose (everything in containers)

```powershell
cd "other\docker"
Copy-Item .env.example .env
notepad .env                      # set DB_PASSWORD; change DB_PORT/BACKEND_PORT/FRONTEND_PORT if busy
docker compose up -d --build      # first build takes a few minutes
docker compose ps                 # postgres + backend should show (healthy)
start http://localhost:8080       # the dashboard
```

What runs:

| Service | Image | Talks to |
|---|---|---|
| `postgres` | postgres:18 | - |
| `backend` | backend/Dockerfile (FastAPI) | `postgres:5432` (service name) |
| `frontend` | other/docker/Dockerfile (nginx + built React app) | proxies `/api` to `backend:8000` |

The backend creates the schema automatically at start-up. In the UI: **Data Generator ->
Generate**, then **Pipeline -> Run pipeline**.

Useful commands:

```powershell
docker compose logs -f backend                    # API + pipeline logs
docker compose exec backend pytest -q             # run the test suite inside the container
docker compose exec backend python -m app.cli benchmark   # performance report
docker compose down                               # stop (data volumes are kept)
docker compose down -v                            # stop and delete all data
```

**`CERTIFICATE_VERIFY_FAILED` during the build?** Antivirus HTTPS scanning (e.g. Avast) or
a corporate proxy is intercepting TLS. See `other/docker/certs/README.md` - export the
root certificate into that folder and build again (verification stays enabled).

---

## Option B - Manual setup

### 1. PostgreSQL 18

Either install PostgreSQL 18 for Windows (EDB installer; it includes the `btree_gist`
extension) and create the database:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE workshop_dw;"
```

or run only the database in Docker:

```powershell
docker run -d --name workshop-postgres -e POSTGRES_PASSWORD=change_me -e POSTGRES_DB=workshop_dw `
  -p 5432:5432 -v workshop_pgdata:/var/lib/postgresql postgres:18
```

The test (`workshop_dw_test`) and benchmark (`workshop_dw_bench`) databases are created
automatically on the same server.

### 2. Backend (FastAPI)

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env                      # DATABASE_URL with your password/port

python -m app.cli init-db         # create schemas raw, source, dw, ops (idempotent)
uvicorn app.main:app --reload --port 8000
```

Check: http://localhost:8000/api/health should show `"connected": true`;
http://localhost:8000/docs lists every endpoint.

### 3. Frontend (React)

In a second terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local # VITE_API_BASE_URL=http://localhost:8000
npm run dev                       # http://localhost:5173
# production build:
npm run build
npm run preview                   # http://localhost:4173
```

If the API runs on a different port, change `VITE_API_BASE_URL` and restart Vite. The
backend's `CORS_ORIGINS` must contain the frontend URL (5173 and 4173 by default).

---

## Datasets

```powershell
cd backend
# small sample (committed in other/datasets/sample): 100 order lines
python -m app.generators.generate_data --customers 40 --products 25 --order-items 100 --seed 42 --output-dir ..\other\datasets\sample
# intermediate: 10,000 orders (~25,000 lines) -> other/datasets/generated
python -m app.generators.generate_data --customers 1000 --products 200 --orders 10000 --seed 42
# 100,000+ order lines
python -m app.generators.generate_data --customers 10000 --products 1000 --order-items 100000
# deliberately invalid data + defect manifest -> other/datasets/invalid
python -m app.generators.generate_data --invalid
```

Or use **Data Generator** in the UI. CSV files can also be uploaded on **Data Sources**
(stored in `other/datasets/uploads`) and loaded with pipeline source "Uploaded files".

## Pipeline from the command line

```powershell
cd backend
python -m app.cli run --input ..\other\datasets\generated
python -m app.cli run --input ..\other\datasets\invalid       # quarantine demo
python -m app.cli runs                                       # history
python -m app.cli reconcile                                  # latest run
python -m app.cli queries --name monthly_revenue             # analytical SQL
python -m app.cli quality-report                             # other/reports/data_quality_report.csv
python -m app.cli demo-scenarios                             # SCD2 / late data demo (isolated DB)
python -m app.cli benchmark                                  # 1K/10K/100K -> other/reports/performance_report.txt
```

## Tests

```powershell
cd backend
pytest -v          # 120 tests; uses <DB_NAME>_test, never your main database
cd ..\frontend
npm run build      # strict TypeScript check + production bundle
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| UI shows "API offline" | Start the backend; check `VITE_API_BASE_URL` (rebuild/restart Vite after changing it). |
| UI says "not the Sales Warehouse API" | Another program uses that port. Run uvicorn on a free port (e.g. `--port 8001`) and point `VITE_API_BASE_URL` at it. |
| `/api/health` shows `connected: false` | PostgreSQL not running or wrong `DATABASE_URL` in `backend/.env`. |
| HTTP 500 "Has the schema been initialised" | `python -m app.cli init-db`. |
| Browser console shows a CORS error | Add the frontend URL to `CORS_ORIGINS` in `backend/.env` and restart the API. |
| `Pipeline ... is still running` (409) | Wait for the current run; only one run at a time is allowed. |
| Port already allocated (Docker) | Change `DB_PORT`, `BACKEND_PORT` or `FRONTEND_PORT` in `other/docker/.env`. |
| `CERTIFICATE_VERIFY_FAILED` in `docker compose build` | See `other/docker/certs/README.md`. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. |

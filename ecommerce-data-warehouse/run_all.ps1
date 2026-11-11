# End-to-end run of the whole project (Windows PowerShell).
#
#   .\run_all.ps1                 # everything except the benchmark
#   .\run_all.ps1 -Benchmark      # also run the 1K/10K/100K benchmark (~2 min)
#
# Prerequisites: .venv created and requirements installed, .env configured,
# Docker Desktop running (or a PostgreSQL 18 server matching .env).
# WARNING: resets the main warehouse database (schemas raw, source, dw, ops).

param([switch]$Benchmark)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Virtual environment not found. Run: py -3.12 -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt" }
if (-not (Test-Path ".env")) { throw ".env not found. Run: Copy-Item .env.example .env  (then set the password/port)" }

function Step([string]$title, [string[]]$arguments) {
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor Cyan
    Write-Host " $title" -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor Cyan
    & $py @arguments
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $title (exit code $LASTEXITCODE)" }
}

Write-Host "Starting PostgreSQL (docker compose up -d) ..."
cmd /c "docker compose up -d 2>&1"   # docker writes progress to stderr; keep PowerShell 5.1 quiet
if ($LASTEXITCODE -ne 0) { Write-Warning "docker compose failed - assuming a PostgreSQL server from .env is already running" }
Start-Sleep -Seconds 3

Step "1. Generate sample dataset (100 order lines)" @("-m", "src.generators.generate_data", "--customers", "40", "--products", "25", "--order-items", "100", "--seed", "42", "--output-dir", "data/sample")
Step "2. Generate intermediate dataset (10,000 orders)" @("-m", "src.generators.generate_data", "--customers", "1000", "--products", "200", "--orders", "10000", "--seed", "42")
Step "3. Generate invalid test dataset" @("-m", "src.generators.generate_data", "--invalid")

Step "4. Initialise database (reset)" @("-m", "src.cli", "init-db", "--reset", "--yes")
Step "5. Pipeline: intermediate dataset (first run)" @("-m", "src.cli", "run", "--input", "data/raw")
Step "6. Pipeline: same dataset again (idempotency - expect 0 inserted)" @("-m", "src.cli", "run", "--input", "data/raw")
Step "7. Pipeline: invalid dataset (quarantine demo, own id namespace)" @("-m", "src.cli", "run", "--input", "data/invalid")
Step "8. Pipeline: intermediate dataset again (latest run reconciles the main load)" @("-m", "src.cli", "run", "--input", "data/raw")
Step "9. Reconciliation of the latest run" @("-m", "src.cli", "reconcile")
Step "10. Analytical queries" @("-m", "src.cli", "queries", "--limit", "10")
Step "11. Pipeline run history" @("-m", "src.cli", "runs")
Step "12. SCD2 / late-arriving / missing-reference scenarios" @("-m", "src.cli", "demo-scenarios")
Step "13. Automated tests" @("-m", "pytest", "-v", "-p", "no:cacheprovider")

if ($Benchmark) {
    Step "14. Performance benchmark (1K / 10K / 100K)" @("-m", "src.cli", "benchmark")
}
Step "15. Dashboard (tables + charts) -> reports\dashboard.html" @("-m", "src.cli", "dashboard")

Write-Host ""
Write-Host "ALL STEPS COMPLETED SUCCESSFULLY" -ForegroundColor Green
Write-Host "Reports: reports\   Logs: logs\pipeline.log"

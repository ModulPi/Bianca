# M4 full stack verification (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_stack.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$composeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.m4.yml")
$tsImage = docker images --format "{{.Repository}}:{{.Tag}}" | Select-String -Pattern "^timescale/timescaledb:latest-pg16$" -Quiet
if (-not $tsImage) {
    Write-Host "WARN: timescale/timescaledb:latest-pg16 not found, using postgres:16-alpine fallback" -ForegroundColor Yellow
    $composeFiles += @("-f", "docker-compose.m4.verify.yml")
}

Write-Host "==> Build and start M4 stack (PG + Redis + API + Web)" -ForegroundColor Cyan
docker compose @composeFiles up -d --build

Write-Host "==> Wait for API health..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 5
        if ($resp.database -eq "ok") {
            $ok = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}

if (-not $ok) {
    Write-Host "FAIL: API health not ready" -ForegroundColor Red
    docker compose @composeFiles logs api --tail 80
    exit 1
}

Write-Host "==> Health response" -ForegroundColor Green
$health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health"
$health | ConvertTo-Json -Depth 4

if ($health.database_backend -ne "postgresql") {
    Write-Host "FAIL: expected database_backend=postgresql" -ForegroundColor Red
    exit 1
}
if ($health.schema_mode -ne "mvp") {
    Write-Host "FAIL: expected schema_mode=mvp" -ForegroundColor Red
    exit 1
}
if ($health.redis -ne "ok") {
    Write-Host "FAIL: expected redis=ok" -ForegroundColor Red
    exit 1
}

Write-Host "==> PostgreSQL tables" -ForegroundColor Cyan
docker compose @composeFiles exec -T postgres psql -U bianca -d bianca -c "\dt"

Write-Host "==> Default agent strategy seed" -ForegroundColor Cyan
$seedSql = "SELECT id, name FROM strategies WHERE id='00000000-0000-4000-8000-000000000001';"
docker compose @composeFiles exec -T postgres psql -U bianca -d bianca -c $seedSql

Write-Host "==> Web console" -ForegroundColor Cyan
try {
    $web = Invoke-WebRequest -Uri "http://127.0.0.1:3000/" -UseBasicParsing -TimeoutSec 10
    if ($web.StatusCode -ne 200) { throw "web status $($web.StatusCode)" }
    Write-Host "Web OK: http://127.0.0.1:3000/" -ForegroundColor Green
} catch {
    Write-Host "WARN: Web not responding: $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "M4 stack verification PASSED" -ForegroundColor Green

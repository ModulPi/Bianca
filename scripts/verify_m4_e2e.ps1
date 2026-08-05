# M4 API E2E verification (PowerShell) — 无需 Binance Key 的 HTTP 链路
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_e2e.ps1
# Live tick（需 .env 中 Binance + LLM Key）:
#   powershell -ExecutionPolicy Bypass -File .\scripts\verify_m4_e2e.ps1 -Live

param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [switch]$Live
)

$ErrorActionPreference = "Stop"
$Api = "$BaseUrl/api/v1"

function Assert-Ok($condition, [string]$message) {
    if (-not $condition) {
        Write-Host "FAIL: $message" -ForegroundColor Red
        exit 1
    }
}

Write-Host "==> M4 API E2E ($Api)" -ForegroundColor Cyan

Write-Host "==> Health" -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "$Api/health" -TimeoutSec 15
$health | ConvertTo-Json -Depth 4
Assert-Ok ($health.database -eq "ok") "database not ok"
Assert-Ok ($health.database_backend -eq "postgresql") "expected postgresql backend"
Assert-Ok ($health.schema_mode -eq "mvp") "expected schema_mode=mvp"
Assert-Ok ($health.checkpointer_backend -eq "postgresql") "expected checkpointer postgresql"
Assert-Ok ($health.redis -eq "ok") "expected redis=ok"

Write-Host "==> Positions (may be empty)" -ForegroundColor Cyan
$positions = Invoke-RestMethod -Uri "$Api/positions" -TimeoutSec 10
Assert-Ok ($positions.schema_mode -eq "mvp") "positions schema_mode"
Write-Host "positions total: $($positions.total)" -ForegroundColor Green

Write-Host "==> Strategy create + list" -ForegroundColor Cyan
$strat = Invoke-RestMethod -Method Post -Uri "$Api/strategies" -ContentType "application/json" `
    -Body '{"name":"E2E趋势","type":"trend","execution_mode":"auto"}' -TimeoutSec 15
Assert-Ok ($null -ne $strat.id) "strategy id missing"
$listing = Invoke-RestMethod -Uri "$Api/strategies" -TimeoutSec 10
Assert-Ok ($listing.total -ge 1) "strategy list empty"
Write-Host "strategies total: $($listing.total)" -ForegroundColor Green

Write-Host "==> Checkpoints threads" -ForegroundColor Cyan
$threads = Invoke-RestMethod -Uri "$Api/checkpoints/threads?limit=10" -TimeoutSec 10
Write-Host "checkpoint threads: $($threads.total)" -ForegroundColor Green

Write-Host "==> Summary latest" -ForegroundColor Cyan
try {
    $summary = Invoke-RestMethod -Uri "$Api/summary/session/latest" -TimeoutSec 10
    Write-Host "latest session: $($summary.session_id)" -ForegroundColor Green
} catch {
    Write-Host "WARN: no session summary yet (start/stop agent once): $_" -ForegroundColor Yellow
}

Write-Host "==> M8 validation / futures / notify" -ForegroundColor Cyan
$validation = Invoke-RestMethod -Uri "$Api/validation/status" -TimeoutSec 10
Assert-Ok ($null -ne $validation.status) "validation status missing"
Write-Host "validation: $($validation.status) · can_live=$($validation.can_enable_live)" -ForegroundColor Green

$futures = Invoke-RestMethod -Uri "$Api/futures/status" -TimeoutSec 10
Assert-Ok ($futures.enabled -eq $false) "expected futures stub disabled"
Write-Host "futures stub: $($futures.message)" -ForegroundColor Green

$notify = Invoke-RestMethod -Uri "$Api/notify/status" -TimeoutSec 10
Write-Host "telegram configured: $($notify.telegram_configured)" -ForegroundColor Green

if ($Live) {
    Write-Host "==> Live agent tick (requires Binance Demo + LLM in .env)" -ForegroundColor Cyan
    $tickBody = @{ thread_id = "e2e-live-$(Get-Date -Format 'yyyyMMddHHmmss')" } | ConvertTo-Json
    $live = Invoke-RestMethod -Method Post -Uri "$Api/agent/tick" -ContentType "application/json" `
        -Body $tickBody -TimeoutSec 120
    Write-Host "live tick status: $($live.status)" -ForegroundColor Green
    $trades = Invoke-RestMethod -Uri "$Api/trades?limit=5" -TimeoutSec 10
    Write-Host "recent trades: $($trades.total)" -ForegroundColor Green
}

Write-Host ""
Write-Host "M4 API E2E PASSED" -ForegroundColor Green

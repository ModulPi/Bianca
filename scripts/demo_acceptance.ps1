# Demo MVP acceptance - Step 1
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\demo_acceptance.ps1

param(
    [string]$Base = "http://127.0.0.1:8000",
    [switch]$StartAgent
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$Api = "$Base/api/v1"

function Step($name, $ok, $detail) {
    $tag = if ($ok) { "[OK]" } else { "[!!]" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host "$tag $name" -ForegroundColor $color
    if ($detail) { Write-Host "    $detail" }
    return [bool]$ok
}

Write-Host ""
Write-Host "=== Bianca Demo Step 1 ===" -ForegroundColor Cyan

$proxyPorts = @(7890, 7897, 10809, 1080)
$openProxy = @()
foreach ($p in $proxyPorts) {
    $r = Test-NetConnection -ComputerName 127.0.0.1 -Port $p -WarningAction SilentlyContinue
    if ($r.TcpTestSucceeded) { $openProxy += $p }
}
if ($openProxy.Count -gt 0) {
    Step "proxy ports" $true ("open: " + ($openProxy -join ", ") + " -> set BINANCE_PROXY in .env")
} else {
    Step "proxy ports" $false "none on 7890/7897/10809/1080 - enable Clash/V2Ray for Binance"
}

try {
    $health = Invoke-RestMethod -Uri "$Api/health" -TimeoutSec 10
    $apiOk = $true
} catch {
    $health = $null
    $apiOk = $false
}
Step "API /health" $apiOk $(if (-not $apiOk) { "start: uvicorn agent.main:app --host 127.0.0.1 --port 8000" })
if (-not $apiOk) { exit 1 }

$llmOk = ($health.llm -eq "ok")
$binanceOk = ($health.binance_demo -eq "ok")
Step "LLM" $llmOk ("status=" + $health.llm + " provider=" + $health.llm_provider)
Step "Binance Demo" $binanceOk ("status=" + $health.binance_demo + " (451 -> BINANCE_PROXY)")

try {
    $snap = Invoke-RestMethod -Uri "$Api/dashboard/snapshot" -TimeoutSec 15
    Step "dashboard snapshot" $true ("generated_at=" + $snap.generated_at)
} catch {
    Step "dashboard snapshot" $false $_.Exception.Message
}

if ($binanceOk) {
    try {
        $t = Invoke-RestMethod -Uri "$Api/exchange/ticker?symbol=BTCUSDT" -TimeoutSec 15
        Step "BTCUSDT ticker" $true ("last=" + $t.last)
    } catch {
        Step "BTCUSDT ticker" $false $_.Exception.Message
    }
} else {
    Step "BTCUSDT ticker" $false "skipped (Binance down)"
}

try {
    $pos = Invoke-RestMethod -Uri "$Api/dashboard/positions" -TimeoutSec 10
    Step "dashboard positions" $true ("total=" + $pos.total)
} catch {
    Step "dashboard positions" $false $_.Exception.Message
}

try {
    $reports = Invoke-RestMethod -Uri "$Api/analysis/reports?limit=5" -TimeoutSec 10
    Step "analysis reports" $true ("total=" + $reports.total)
} catch {
    Step "analysis reports" $false $_.Exception.Message
}

try {
    $inProg = Invoke-RestMethod -Uri "$Api/trades?status=in_progress&limit=5" -TimeoutSec 10
    Step "trades in_progress alias" $true ("items=" + @($inProg.items).Count)
} catch {
    Step "trades in_progress alias" $false $_.Exception.Message
}

$status = Invoke-RestMethod -Uri "$Api/agent/status" -TimeoutSec 10
if ($StartAgent -and -not $status.running) {
    if (-not $llmOk) {
        Step "agent start" $false "LLM not ready"
    } elseif (-not $binanceOk) {
        Step "agent start" $false "Binance not ready"
    } else {
        $r = Invoke-RestMethod -Method Post -Uri "$Api/agent/start" -TimeoutSec 15
        Step "agent start" $true $r.message
        $status = Invoke-RestMethod -Uri "$Api/agent/status" -TimeoutSec 10
    }
} else {
    Step "agent status" $true ("running=" + $status.running + " ticks=" + $status.tick_count)
}

try {
    $trades = Invoke-RestMethod -Uri "$Api/trades?limit=20" -TimeoutSec 10
    $items = @($trades.items)
    $buys = ($items | Where-Object { $_.side -eq "BUY" -and $_.status -eq "filled" }).Count
    $sells = ($items | Where-Object { $_.side -eq "SELL" -and $_.status -eq "filled" }).Count
    $loopOk = ($buys -ge 1 -and $sells -ge 1)
    Step "PoC loop BUY+SELL" $loopOk ("BUY=" + $buys + " SELL=" + $sells)
} catch {
    Step "trades" $false $_.Exception.Message
}

Write-Host ""
Write-Host "--- next ---" -ForegroundColor Cyan
if (-not $binanceOk) {
    Write-Host "1. Start proxy, uncomment BINANCE_PROXY in .env, restart API"
    Write-Host "2. Re-run: powershell -File .\scripts\demo_acceptance.ps1"
}
if ($binanceOk -and $llmOk) {
    Write-Host "Run closure: D:\Python311\python.exe run_poc_closure.py"
    Write-Host "Or: powershell -File .\scripts\demo_acceptance.ps1 -StartAgent"
}
Write-Host ""

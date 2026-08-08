# 启动 M4 全栈（PostgreSQL + Redis + API + Web）
param(
    [switch]$Build,
    [switch]$WithMonitoring
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$composeFiles = @(
    "-f", "docker-compose.yml",
    "-f", "deploy/compose/m4.yml"
)
if ($WithMonitoring) {
    $composeFiles += @("-f", "deploy/compose/mvp.yml", "--profile", "mvp")
}

$tsImage = docker images --format "{{.Repository}}:{{.Tag}}" | Select-String -Pattern "^timescale/timescaledb:latest-pg16$" -Quiet
if (-not $tsImage) {
    Write-Host "WARN: timescale 镜像未找到，使用 postgres:16-alpine 降级" -ForegroundColor Yellow
    $composeFiles += @("-f", "deploy/compose/m4.verify.yml")
}

$args = @("compose") + $composeFiles + @("up", "-d")
if ($Build) { $args += "--build" }
docker @args

Write-Host "API  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Web  http://127.0.0.1:3001" -ForegroundColor Green
Write-Host "PG   127.0.0.1:5433" -ForegroundColor Green

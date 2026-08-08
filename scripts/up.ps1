# 启动基础栈（API + Web，SQLite）
param(
    [switch]$Build
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$args = @("compose", "up", "-d")
if ($Build) { $args += "--build" }
docker @args
Write-Host "API  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Web  http://127.0.0.1:3001" -ForegroundColor Green

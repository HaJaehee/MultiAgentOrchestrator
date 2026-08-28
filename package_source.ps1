# Multi-Agent Orchestrator Platform - Source & Config Only Packaging Script
# 런타임(포터블 파이썬 / node / wheel / MCP 서버)은 빼고 소스와 설정만 dist/ 에 압축합니다.
$ErrorActionPreference = "Stop"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$RootDir = $PSScriptRoot
if (-not $RootDir) { $RootDir = (Get-Location).Path }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# 인자는 그대로 전달됩니다. 예: .\package_source.ps1 --no-tests --no-docs
python (Join-Path $RootDir "package_source.py") @args

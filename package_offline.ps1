# Multi-Agent Orchestrator Platform - Offline Packaging Script
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

# 인자는 그대로 전달됩니다. 예: .\package_offline.ps1 --skip-node
python (Join-Path $RootDir "package_offline.py") @args

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

python (Join-Path $RootDir "package_offline.py")

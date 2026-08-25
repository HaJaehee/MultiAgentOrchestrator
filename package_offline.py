import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"
STAGING_DIR = DIST_DIR / "MultiAgentOrchestrator_bundle"
ZIP_FILE = DIST_DIR / "MultiAgentOrchestrator_offline.zip"


def main():
    print("=" * 60)
    print("  [폐쇄망 배포용] Multi-Agent Orchestrator 패키징 시작")
    print("=" * 60)

    # 1. Prepare Staging Directories
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    staging_app = STAGING_DIR / "app"
    staging_wheels = STAGING_DIR / "wheels"
    staging_runtime = STAGING_DIR / "python_runtime"

    staging_app.mkdir(parents=True, exist_ok=True)
    staging_wheels.mkdir(parents=True, exist_ok=True)
    staging_runtime.mkdir(parents=True, exist_ok=True)

    # 2. Copy Source Code and Configurations
    print("[1/5] 소스코드 및 설정 파일 복사 중...")
    if staging_app.exists():
        shutil.rmtree(staging_app)
    shutil.copytree(ROOT_DIR / "app", staging_app)

    for fname in ["conf.toml", ".env.example", "requirements.txt", "README.md"]:
        src = ROOT_DIR / fname
        if src.exists():
            shutil.copy2(src, STAGING_DIR / fname)

    # 3. Collect Pip Wheels
    existing_wheels = list(staging_wheels.glob("*.whl"))
    if len(existing_wheels) < 20:
        print("[2/5] 오프라인 pip Wheel 패키지 수집 중 (requirements.txt)...")
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "-r", str(ROOT_DIR / "requirements.txt"), "-d", str(staging_wheels)],
            check=True,
        )
    else:
        print(f"[2/5] 기존 수집된 Wheel 패키지 ({len(existing_wheels)}개) 확인 완료.")

    # 4. Bundle Portable Python Runtime
    py_exe_target = staging_runtime / "python.exe"
    if not py_exe_target.exists():
        print("[3/5] 포터블 파이썬 런타임 및 의존성 라이브러리 복제 중...")
        py_home = Path(sys.executable).parent
        runtime_items = ["python.exe", "pythonw.exe", "DLLs", "Lib", "Scripts"]
        for item in runtime_items:
            src = py_home / item
            dst = staging_runtime / item
            if src.exists():
                if src.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        # Copy dlls in root of python home
        for dll in py_home.glob("*.dll"):
            shutil.copy2(dll, staging_runtime / dll.name)
    else:
        print("[3/5] 포터블 파이썬 런타임 번들 확인 완료.")

    # 5. Write Launch Scripts with UTF-8 BOM
    print("[4/5] 폐쇄망 전용 실행 스크립트 작성 중 (UTF-8 BOM)...")

    # 5.1 run_offline.bat
    bat_content = (
        "@echo off\r\n"
        "chcp 65001 > nul\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n\r\n"
        "echo ==========================================================\r\n"
        "echo   Multi-Agent Orchestrator Platform (오프라인 / 폐쇄망 모드)\r\n"
        "echo ==========================================================\r\n\r\n"
        "set \"PYTHONHOME=%~dp0python_runtime\"\r\n"
        "set \"PYTHONPATH=%~dp0;%~dp0python_runtime\\Lib;%~dp0python_runtime\\Lib\\site-packages\"\r\n"
        "set \"PATH=%~dp0python_runtime;%~dp0python_runtime\\Scripts;%PATH%\"\r\n"
        "set \"PYTHONIOENCODING=utf-8\"\r\n"
        "set \"PYTHONUTF8=1\"\r\n\r\n"
        "echo [*] 내장 포터블 파이썬 런타임으로 서버를 시작합니다 (http://127.0.0.1:8000)...\r\n"
        "\"%~dp0python_runtime\\python.exe\" -m app.main\r\n\r\n"
        "pause\r\n"
    )
    with open(STAGING_DIR / "run_offline.bat", "w", encoding="utf-8-sig", newline="") as f:
        f.write(bat_content)

    # 5.2 run_offline.ps1 (With explicit UTF-8 Console and UTF-8 BOM)
    ps1_content = (
        "# Multi-Agent Orchestrator Platform - Offline Launch Script\r\n"
        "$ErrorActionPreference = \"Stop\"\r\n\r\n"
        "# 콘솔 입출력 인코딩 UTF-8 설정 (한글 깨짐 방지)\r\n"
        "try {\r\n"
        "    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\r\n"
        "    [Console]::InputEncoding = [System.Text.Encoding]::UTF8\r\n"
        "    $OutputEncoding = [System.Text.Encoding]::UTF8\r\n"
        "} catch {}\r\n\r\n"
        "$RootDir = $PSScriptRoot\r\n"
        "if (-not $RootDir) { $RootDir = (Get-Location).Path }\r\n\r\n"
        "Write-Host \"==========================================================\" -ForegroundColor Cyan\r\n"
        "Write-Host \"  Multi-Agent Orchestrator Platform (오프라인 / 폐쇄망 모드)\" -ForegroundColor Cyan\r\n"
        "Write-Host \"==========================================================\" -ForegroundColor Cyan\r\n\r\n"
        "$env:PYTHONHOME = Join-Path $RootDir \"python_runtime\"\r\n"
        "$env:PYTHONPATH = \"$RootDir;$(Join-Path $RootDir 'python_runtime\\Lib');$(Join-Path $RootDir 'python_runtime\\Lib\\site-packages')\"\r\n"
        "$env:PATH = \"$(Join-Path $RootDir 'python_runtime');$(Join-Path $RootDir 'python_runtime\\Scripts');\" + $env:PATH\r\n"
        "$env:PYTHONIOENCODING = \"utf-8\"\r\n"
        "$env:PYTHONUTF8 = \"1\"\r\n\r\n"
        "$pyExe = Join-Path $RootDir \"python_runtime\\python.exe\"\r\n\r\n"
        "Write-Host \"[*] 내장 파이썬 런타임으로 서버를 시작합니다 (http://127.0.0.1:8000)...\" -ForegroundColor Green\r\n"
        "& $pyExe -m app.main\r\n"
    )
    with open(STAGING_DIR / "run_offline.ps1", "w", encoding="utf-8-sig", newline="") as f:
        f.write(ps1_content)

    # 5.3 install_wheels_offline.bat
    install_bat = (
        "@echo off\r\n"
        "chcp 65001 > nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo [*] wheels 디렉토리의 오프라인 패키지를 로컬 환경에 설치합니다...\r\n"
        "pip install --no-index --find-links=.\\wheels -r requirements.txt\r\n"
        "pause\r\n"
    )
    with open(STAGING_DIR / "install_wheels_offline.bat", "w", encoding="utf-8-sig", newline="") as f:
        f.write(install_bat)

    # 5.4 README_OFFLINE.md
    readme_offline = (
        "# 📦 Multi-Agent Orchestrator Platform - 폐쇄망(오프라인) 배포 가이드\r\n\r\n"
        "이 패키지는 외부 인터넷 연결이 불가능한 폐쇄망 환경에서도 즉시 구동될 수 있도록 파이썬 런타임, 의존성 라이브러리, 오프라인 Wheel 파일 및 소스코드를 모두 포함하고 있습니다.\r\n\r\n"
        "## 🚀 빠른 실행 방법\r\n\r\n"
        "### 방법 1. 내장 포터블 런타임으로 즉시 실행 (가장 추천)\r\n"
        "추가 설치나 환경 설정 없이 압축을 해제한 폴더에서 바로 실행할 수 있습니다.\r\n"
        "- Windows 탐색기에서 **`run_offline.bat`** 더블 클릭  \r\n"
        "  또는 PowerShell에서:\r\n"
        "  ```powershell\r\n"
        "  .\\run_offline.ps1\r\n"
        "  ```\r\n"
        "- 실행 후 브라우저에서 **`http://127.0.0.1:8000`** 접속.\r\n\r\n"
        "---\r\n\r\n"
        "### 방법 2. 폐쇄망 내 기존 Python 환경에 Wheel 설치하여 실행\r\n"
        "이미 폐쇄망 PC에 별도 Python이 설치되어 있는 경우:\r\n"
        "1. `install_wheels_offline.bat` 실행 (인터넷 없이 `.\\wheels\\` 폴더에서 오프라인 설치)\r\n"
        "2. `python -m app.main` 실행\r\n\r\n"
        "---\r\n\r\n"
        "## ⚙️ 설정 커스텀\r\n"
        "- `conf.toml`: 에이전트 목록, 프롬프트, 라운드 수, MCP 서버 설정.\r\n"
        "- `.env`: LLM API 키(폐쇄망 로컬 LLM / Ollama / vLLM 주소 등) 설정.\r\n"
    )
    with open(STAGING_DIR / "README_OFFLINE.md", "w", encoding="utf-8-sig", newline="") as f:
        f.write(readme_offline)

    # 6. Compress ZIP
    print(f"[5/5] 최종 ZIP 아카이브 생성 중 ({ZIP_FILE})...")
    if ZIP_FILE.exists():
        ZIP_FILE.unlink()

    base_name = str(DIST_DIR / "MultiAgentOrchestrator_offline")
    shutil.make_archive(base_name, "zip", STAGING_DIR)

    zip_size_mb = round(ZIP_FILE.stat().st_size / (1024 * 1024), 2)

    print("=" * 60)
    print(" [완료] 폐쇄망 패키징이 성공적으로 생성되었습니다!")
    print(f"  - 아카이브 경로: {ZIP_FILE}")
    print(f"  - 파일 크기: {zip_size_mb} MB")
    print("  - 포함 구성: 소스코드, conf.toml, 포터블 Python 런타임, 오프라인 Wheels, 실행 스크립트")
    print("=" * 60)


if __name__ == "__main__":
    main()

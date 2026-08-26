"""폐쇄망(에어갭) 배포용 번들 패키징 스크립트.

생성되는 번들에는 다음이 모두 포함되어 인터넷 없이 즉시 구동됩니다.

    MultiAgentOrchestrator_bundle/
    ├── app/                 애플리케이션 소스
    ├── conf.toml            설정 (없으면 conf.example.toml 에서 복사)
    ├── wheels/              오프라인 pip wheel (앱 + MCP 서버 + 샌드박스 서버)
    ├── python_runtime/      포터블 CPython
    ├── node_runtime/        node.exe (MCP 공식 Node 서버 구동용, npm 미포함)
    ├── mcp_node/            filesystem / memory / sequential-thinking MCP 서버
    ├── mcp_sandbox/         AirgappedPySandbox (Python 코드 실행 MCP 서버)
    ├── workspace/           에이전트 공용 작업 공간
    └── run_offline.bat|ps1  실행 스크립트 (MCP 경로 환경변수 자동 주입)

사용법:
    python package_offline.py [--skip-node] [--node-version 22.22.2]
                              [--sandbox-src <경로>] [--skip-sandbox]

이 스크립트는 **Windows 에서 실행**하는 것을 전제로 합니다. 포터블 런타임과
pip wheel 이 실행 플랫폼 기준으로 수집되기 때문입니다. 패키징 시점에는 인터넷이
필요하며(pip download / npm install / node 내려받기), 산출물 실행 시점에는
필요하지 않습니다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"
STAGING_DIR = DIST_DIR / "MultiAgentOrchestrator_bundle"
ZIP_FILE = DIST_DIR / "MultiAgentOrchestrator_offline.zip"

# 번들에 포함할 Node 런타임. 공식 MCP 서버들은 순수 JS 라 node.exe 하나면 돌아가며
# npm / npx 는 런타임에 전혀 필요하지 않습니다.
DEFAULT_NODE_VERSION = "22.22.2"
NODE_DIST_URL = "https://nodejs.org/dist/v{ver}/node-v{ver}-win-x64.zip"

# 공식 레퍼런스 MCP 서버 (TypeScript 구현). 네이티브 애드온이 없어 어느 OS 에서
# 설치해도 그대로 재배치됩니다.
NODE_MCP_PACKAGES = [
    "@modelcontextprotocol/server-filesystem",
    "@modelcontextprotocol/server-memory",
    "@modelcontextprotocol/server-sequential-thinking",
]

# 공식 레퍼런스 MCP 서버 중 Python 구현. 기존 wheel 파이프라인에 그대로 얹힙니다.
PYTHON_MCP_PACKAGES = [
    "mcp-server-fetch",
    "mcp-server-git",
]

# Python 코드 실행 샌드박스 MCP 서버.
SANDBOX_REPO_URL = "https://github.com/HaJaehee/AirgappedPySandbox"
SANDBOX_EXCLUDE = shutil.ignore_patterns(
    ".git", ".github", "__pycache__", "*.pyc", "dist", "build", "workspace", ".venv"
)

STEPS = 8


def log(step: int, message: str) -> None:
    print(f"[{step}/{STEPS}] {message}")


# ---------------------------------------------------------------------------
# 1. 소스 및 설정
# ---------------------------------------------------------------------------
def stage_sources() -> None:
    staging_app = STAGING_DIR / "app"
    if staging_app.exists():
        shutil.rmtree(staging_app)
    shutil.copytree(ROOT_DIR / "app", staging_app, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    for fname in [".env.example", "requirements.txt", "README.md"]:
        src = ROOT_DIR / fname
        if src.exists():
            shutil.copy2(src, STAGING_DIR / fname)

    # conf.toml 은 gitignore 대상이라 신규 클론에는 없습니다. 템플릿으로 대체합니다.
    conf_src = ROOT_DIR / "conf.toml"
    if not conf_src.exists():
        conf_src = ROOT_DIR / "conf.example.toml"
        print("      conf.toml 이 없어 conf.example.toml 을 번들 설정으로 사용합니다.")
    if not conf_src.exists():
        raise FileNotFoundError("conf.toml / conf.example.toml 을 찾을 수 없습니다.")
    shutil.copy2(conf_src, STAGING_DIR / "conf.toml")

    # 에이전트 공용 작업 공간 (filesystem / git / sandbox MCP 가 공유)
    workspace = STAGING_DIR / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".gitkeep").touch()


# ---------------------------------------------------------------------------
# 2. Python 코드 실행 샌드박스 MCP 서버
# ---------------------------------------------------------------------------
def stage_sandbox(sandbox_src: str | None) -> Path | None:
    """AirgappedPySandbox 를 mcp_sandbox/ 로 벤더링하고 소스 경로를 반환합니다."""
    target = STAGING_DIR / "mcp_sandbox"

    candidates = []
    if sandbox_src:
        candidates.append(Path(sandbox_src).expanduser())
    if os.environ.get("MCP_SANDBOX_SRC"):
        candidates.append(Path(os.environ["MCP_SANDBOX_SRC"]).expanduser())
    candidates += [
        ROOT_DIR.parent / "AirgappedPySandbox",
        ROOT_DIR.parent / "airgappedpysandbox",
    ]

    source = next((c for c in candidates if (c / "server.py").exists()), None)

    if source is None:
        # 로컬에 없으면 패키징 시점의 인터넷으로 얕은 클론을 시도합니다.
        checkout = DIST_DIR / "_airgapped_sandbox_src"
        if not (checkout / "server.py").exists():
            print(f"      로컬에서 찾지 못해 클론을 시도합니다: {SANDBOX_REPO_URL}")
            if checkout.exists():
                shutil.rmtree(checkout)
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", SANDBOX_REPO_URL, str(checkout)],
                    check=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                print(f"      [경고] 샌드박스 서버를 준비하지 못했습니다: {exc}")
                print("             --sandbox-src <경로> 로 로컬 체크아웃을 지정하거나")
                print("             --skip-sandbox 로 건너뛸 수 있습니다.")
                return None
        source = checkout

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=SANDBOX_EXCLUDE)
    (target / "workspace").mkdir(exist_ok=True)
    print(f"      샌드박스 서버 원본: {source}")
    return source


# ---------------------------------------------------------------------------
# 3. pip wheel 수집
# ---------------------------------------------------------------------------
def collect_wheels(sandbox_source: Path | None) -> None:
    staging_wheels = STAGING_DIR / "wheels"
    staging_wheels.mkdir(parents=True, exist_ok=True)

    marker = staging_wheels / ".collected"
    if marker.exists():
        count = len(list(staging_wheels.glob("*.whl")))
        print(f"      기존 수집분({count}개) 재사용. 다시 받으려면 wheels/.collected 를 지우세요.")
        return

    def pip_download(args: list[str], label: str) -> None:
        print(f"      - {label}")
        subprocess.run(
            [sys.executable, "-m", "pip", "download", *args, "-d", str(staging_wheels)],
            check=True,
        )

    pip_download(["-r", str(ROOT_DIR / "requirements.txt")], "애플리케이션 의존성")
    pip_download(PYTHON_MCP_PACKAGES, "공식 Python MCP 서버 (fetch / git)")

    if sandbox_source is not None:
        req = sandbox_source / "requirements-server.txt"
        if req.exists():
            pip_download(["-r", str(req)], "샌드박스 MCP 서버 의존성 (jupyter_client / ipykernel)")

    marker.touch()
    print(f"      총 {len(list(staging_wheels.glob('*.whl')))}개 wheel 수집 완료.")


# ---------------------------------------------------------------------------
# 4. 포터블 Python 런타임
# ---------------------------------------------------------------------------
def stage_python_runtime() -> None:
    staging_runtime = STAGING_DIR / "python_runtime"
    staging_runtime.mkdir(parents=True, exist_ok=True)

    if (staging_runtime / "python.exe").exists():
        print("      기존 번들 확인 완료.")
        return

    py_home = Path(sys.executable).parent
    for item in ["python.exe", "pythonw.exe", "DLLs", "Lib", "Scripts"]:
        src = py_home / item
        dst = staging_runtime / item
        if not src.exists():
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)

    for dll in py_home.glob("*.dll"):
        shutil.copy2(dll, staging_runtime / dll.name)


# ---------------------------------------------------------------------------
# 5. Node 런타임 (node.exe 단일 파일)
# ---------------------------------------------------------------------------
def stage_node_runtime(node_version: str) -> bool:
    staging_node = STAGING_DIR / "node_runtime"
    staging_node.mkdir(parents=True, exist_ok=True)
    node_exe = staging_node / "node.exe"

    if node_exe.exists():
        print("      기존 번들 확인 완료.")
        return True

    url = NODE_DIST_URL.format(ver=node_version)
    archive = DIST_DIR / f"node-v{node_version}-win-x64.zip"

    if not archive.exists():
        print(f"      내려받는 중: {url}")
        try:
            urllib.request.urlretrieve(url, archive)
        except Exception as exc:  # noqa: BLE001 - 네트워크/프록시 오류 전부 안내로 전환
            print(f"      [경고] Node 런타임 다운로드 실패: {exc}")
            local = shutil.which("node")
            if local and sys.platform == "win32":
                shutil.copy2(local, node_exe)
                print(f"      대신 로컬 node 를 복사했습니다: {local}")
                return True
            print(f"             {url} 를 수동으로 받아 {archive} 에 두고 다시 실행하세요.")
            return False

    # 압축 전체를 풀지 않고 node.exe 만 꺼냅니다 (npm/npx 는 런타임에 불필요).
    with zipfile.ZipFile(archive) as zf:
        member = next((n for n in zf.namelist() if n.endswith("/node.exe")), None)
        if member is None:
            print("      [경고] 아카이브에서 node.exe 를 찾지 못했습니다.")
            return False
        with zf.open(member) as src, open(node_exe, "wb") as dst:
            shutil.copyfileobj(src, dst)

    size_mb = round(node_exe.stat().st_size / (1024 * 1024), 1)
    print(f"      node.exe 추출 완료 (v{node_version}, {size_mb} MB)")
    return True


# ---------------------------------------------------------------------------
# 6. 공식 Node MCP 서버
# ---------------------------------------------------------------------------
def stage_node_mcp_servers() -> bool:
    staging_mcp = STAGING_DIR / "mcp_node"
    staging_mcp.mkdir(parents=True, exist_ok=True)

    if (staging_mcp / "node_modules" / "@modelcontextprotocol").exists():
        print("      기존 설치 확인 완료.")
        return True

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        print("      [경고] npm 을 찾을 수 없어 Node MCP 서버를 건너뜁니다.")
        print("             filesystem / memory MCP 없이 번들이 만들어집니다.")
        return False

    print(f"      설치 중: {', '.join(p.rsplit('/', 1)[-1] for p in NODE_MCP_PACKAGES)}")
    try:
        subprocess.run(
            [npm, "install", "--omit=dev", "--no-audit", "--no-fund",
             "--prefix", str(staging_mcp), *NODE_MCP_PACKAGES],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"      [경고] npm install 실패: {exc}")
        return False

    # .bin 셰이딩은 OS 별 심볼릭 링크/셰이더라 재배치에 방해만 됩니다.
    # 서버는 dist/index.js 를 node 로 직접 실행하므로 필요 없습니다.
    bin_dir = staging_mcp / "node_modules" / ".bin"
    if bin_dir.exists():
        shutil.rmtree(bin_dir, ignore_errors=True)

    count = sum(1 for _ in (staging_mcp / "node_modules").iterdir())
    print(f"      node_modules 준비 완료 ({count}개 패키지).")
    return True


# ---------------------------------------------------------------------------
# 7. 실행 스크립트 & 문서
# ---------------------------------------------------------------------------
def write_launchers(has_node: bool, has_sandbox: bool) -> None:
    def write(name: str, content: str) -> None:
        with open(STAGING_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            f.write(content)

    # --- run_offline.bat ---
    bat = (
        "@echo off\r\n"
        "chcp 65001 > nul\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n\r\n"
        "echo ==========================================================\r\n"
        "echo   Multi-Agent Orchestrator Platform (오프라인 / 폐쇄망 모드)\r\n"
        "echo ==========================================================\r\n\r\n"
        "set \"PYTHONHOME=%~dp0python_runtime\"\r\n"
        "set \"PYTHONPATH=%~dp0;%~dp0python_runtime\\Lib;%~dp0python_runtime\\Lib\\site-packages\"\r\n"
        "set \"PATH=%~dp0python_runtime;%~dp0python_runtime\\Scripts;%~dp0node_runtime;%PATH%\"\r\n"
        "set \"PYTHONIOENCODING=utf-8\"\r\n"
        "set \"PYTHONUTF8=1\"\r\n\r\n"
        "rem --- MCP 서버 실행 경로 (conf.toml 의 ${VAR:-기본값} 치환에 사용) ---\r\n"
        "set \"PYTHON_BIN=%~dp0python_runtime\\python.exe\"\r\n"
        "set \"NODE_BIN=%~dp0node_runtime\\node.exe\"\r\n"
        "set \"MCP_NODE_HOME=%~dp0mcp_node\"\r\n"
        "set \"MCP_SANDBOX_HOME=%~dp0mcp_sandbox\"\r\n"
        "set \"WORKSPACE_DIR=%~dp0workspace\"\r\n"
        "if not defined SANDBOX_KERNEL_PYTHON set \"SANDBOX_KERNEL_PYTHON=%PYTHON_BIN%\"\r\n\r\n"
        "if not exist \"%NODE_BIN%\" echo [!] node_runtime\\node.exe 가 없습니다. "
        "filesystem / memory MCP 가 비활성화됩니다.\r\n\r\n"
        "echo [*] 내장 포터블 파이썬 런타임으로 서버를 시작합니다 (http://127.0.0.1:8000)...\r\n"
        "\"%PYTHON_BIN%\" -m app.main\r\n\r\n"
        "pause\r\n"
    )
    write("run_offline.bat", bat)

    # --- run_offline.ps1 ---
    ps1 = (
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
        "$env:PATH = \"$(Join-Path $RootDir 'python_runtime');$(Join-Path $RootDir 'python_runtime\\Scripts');$(Join-Path $RootDir 'node_runtime');\" + $env:PATH\r\n"
        "$env:PYTHONIOENCODING = \"utf-8\"\r\n"
        "$env:PYTHONUTF8 = \"1\"\r\n\r\n"
        "# --- MCP 서버 실행 경로 (conf.toml 의 ${VAR:-기본값} 치환에 사용) ---\r\n"
        "$env:PYTHON_BIN = Join-Path $RootDir \"python_runtime\\python.exe\"\r\n"
        "$env:NODE_BIN = Join-Path $RootDir \"node_runtime\\node.exe\"\r\n"
        "$env:MCP_NODE_HOME = Join-Path $RootDir \"mcp_node\"\r\n"
        "$env:MCP_SANDBOX_HOME = Join-Path $RootDir \"mcp_sandbox\"\r\n"
        "$env:WORKSPACE_DIR = Join-Path $RootDir \"workspace\"\r\n"
        "if (-not $env:SANDBOX_KERNEL_PYTHON) { $env:SANDBOX_KERNEL_PYTHON = $env:PYTHON_BIN }\r\n\r\n"
        "if (-not (Test-Path $env:NODE_BIN)) {\r\n"
        "    Write-Warning \"node_runtime\\node.exe 가 없습니다. filesystem / memory MCP 가 비활성화됩니다.\"\r\n"
        "}\r\n\r\n"
        "Write-Host \"[*] 내장 파이썬 런타임으로 서버를 시작합니다 (http://127.0.0.1:8000)...\" -ForegroundColor Green\r\n"
        "& $env:PYTHON_BIN -m app.main\r\n"
    )
    write("run_offline.ps1", ps1)

    # --- install_wheels_offline.bat ---
    install_bat = (
        "@echo off\r\n"
        "chcp 65001 > nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo [*] wheels 디렉토리의 오프라인 패키지를 로컬 환경에 설치합니다...\r\n"
        "pip install --no-index --find-links=.\\wheels -r requirements.txt\r\n"
        "echo [*] MCP 서버 패키지(fetch / git / 샌드박스 커널)를 설치합니다...\r\n"
        "pip install --no-index --find-links=.\\wheels "
        "mcp-server-fetch mcp-server-git jupyter_client ipykernel\r\n"
        "echo.\r\n"
        "echo [!] 이 방식으로 실행할 때는 conf.toml 이 참조하는 아래 환경변수를 직접 지정하거나\r\n"
        "echo     PATH 에 node 를 등록해야 filesystem / memory MCP 가 동작합니다.\r\n"
        "echo       NODE_BIN / PYTHON_BIN / MCP_NODE_HOME / MCP_SANDBOX_HOME / WORKSPACE_DIR\r\n"
        "pause\r\n"
    )
    write("install_wheels_offline.bat", install_bat)

    # --- README_OFFLINE.md ---
    node_line = (
        "- `node_runtime/` + `mcp_node/` : filesystem · memory · sequential-thinking MCP 서버 (Node 순수 JS)\r\n"
        if has_node
        else "- [!] Node MCP 서버가 번들에 포함되지 않았습니다 (패키징 시 npm 또는 node 다운로드 실패).\r\n"
    )
    sandbox_line = (
        "- `mcp_sandbox/` : AirgappedPySandbox — 상태 유지형 Python 코드 실행 MCP 서버\r\n"
        if has_sandbox
        else "- [!] 코드 실행 샌드박스가 번들에 포함되지 않았습니다.\r\n"
    )
    readme_offline = (
        "# 📦 Multi-Agent Orchestrator Platform - 폐쇄망(오프라인) 배포 가이드\r\n\r\n"
        "이 패키지는 외부 인터넷 연결이 불가능한 폐쇄망 환경에서도 즉시 구동될 수 있도록 "
        "파이썬 런타임, Node 런타임, MCP 서버, 의존성 라이브러리, 오프라인 Wheel 파일 및 "
        "소스코드를 모두 포함하고 있습니다.\r\n\r\n"
        "## 🚀 빠른 실행 방법\r\n\r\n"
        "### 방법 1. 내장 포터블 런타임으로 즉시 실행 (가장 추천)\r\n"
        "추가 설치나 환경 설정 없이 압축을 해제한 폴더에서 바로 실행할 수 있습니다.\r\n"
        "- Windows 탐색기에서 **`run_offline.bat`** 더블 클릭  \r\n"
        "  또는 PowerShell에서:\r\n"
        "  ```powershell\r\n"
        "  .\\run_offline.ps1\r\n"
        "  ```\r\n"
        "- 실행 후 브라우저에서 **`http://127.0.0.1:8000`** 접속.\r\n\r\n"
        "실행 스크립트가 MCP 서버 경로 환경변수(`NODE_BIN`, `PYTHON_BIN`, `MCP_NODE_HOME`, "
        "`MCP_SANDBOX_HOME`, `WORKSPACE_DIR`)를 자동으로 채워주므로 별도 설정이 필요 없습니다.\r\n\r\n"
        "---\r\n\r\n"
        "### 방법 2. 폐쇄망 내 기존 Python 환경에 Wheel 설치하여 실행\r\n"
        "이미 폐쇄망 PC에 별도 Python이 설치되어 있는 경우:\r\n"
        "1. `install_wheels_offline.bat` 실행 (인터넷 없이 `.\\wheels\\` 폴더에서 오프라인 설치)\r\n"
        "2. 위 환경변수를 직접 지정 (특히 `NODE_BIN`, `MCP_NODE_HOME`)\r\n"
        "3. `python -m app.main` 실행\r\n\r\n"
        "---\r\n\r\n"
        "## 🧰 포함된 MCP 도구\r\n\r\n"
        + node_line
        + sandbox_line
        + "- `wheels/` 에 포함된 `mcp-server-git` : 산출물 버전 관리 (기본 활성)\r\n"
        "- `wheels/` 에 포함된 `mcp-server-fetch` : URL 조회 (기본 **비활성** — 폐쇄망에서는 켜지 마세요)\r\n\r\n"
        "MCP 서버는 `conf.toml` 의 `[mcp_servers.*]` 에서 `enabled = false` 로 개별 비활성화할 수 있습니다.\r\n"
        "모든 서버는 `node`/`python` 진입점을 **직접** 실행합니다. `npx` 는 패키지가 로컬에 없으면 "
        "npm 레지스트리에 접속하므로 폐쇄망에서 사용하지 않습니다.\r\n\r\n"
        "### 코드 실행 샌드박스 커널\r\n"
        "`mcp_sandbox` 는 기본적으로 번들 내장 파이썬으로 커널을 띄웁니다. pandas·numpy·matplotlib 등을 "
        "쓰는 코드까지 검증하려면 해당 라이브러리가 포함된 포터블 파이썬 경로를 "
        "`SANDBOX_KERNEL_PYTHON` 환경변수로 지정하세요.\r\n\r\n"
        "---\r\n\r\n"
        "## ⚙️ 설정 커스텀\r\n"
        "- `conf.toml`: 에이전트 목록, 프롬프트, 라운드 수, MCP 서버 설정.\r\n"
        "- `.env`: LLM API 키(폐쇄망 로컬 LLM / Ollama / vLLM 주소 등) 설정.\r\n"
        "- `workspace/`: 에이전트가 파일을 읽고 쓰는 공용 작업 공간. filesystem · git · sandbox MCP 가 "
        "이 디렉터리를 공유합니다.\r\n"
    )
    write("README_OFFLINE.md", readme_offline)


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="폐쇄망 배포용 번들 패키징")
    parser.add_argument("--skip-node", action="store_true", help="Node 런타임 및 Node MCP 서버 제외")
    parser.add_argument("--skip-sandbox", action="store_true", help="코드 실행 샌드박스 MCP 서버 제외")
    parser.add_argument("--node-version", default=DEFAULT_NODE_VERSION, help="번들할 Node 버전")
    parser.add_argument("--sandbox-src", default=None, help="AirgappedPySandbox 로컬 체크아웃 경로")
    args = parser.parse_args()

    print("=" * 60)
    print("  [폐쇄망 배포용] Multi-Agent Orchestrator 패키징 시작")
    print("=" * 60)

    if sys.platform != "win32":
        print("[!] 경고: Windows 가 아닌 환경입니다.")
        print("    포터블 런타임과 pip wheel 이 현재 플랫폼 기준으로 수집되므로")
        print("    Windows 대상 번들을 만들려면 Windows 에서 실행하세요.")
        print("    (Node MCP 서버는 순수 JS 라 어느 OS 에서 설치해도 동일합니다.)")
        print()

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    log(1, "소스코드 및 설정 파일 복사 중...")
    stage_sources()

    log(2, "코드 실행 샌드박스 MCP 서버(AirgappedPySandbox) 준비 중...")
    sandbox_source = None if args.skip_sandbox else stage_sandbox(args.sandbox_src)
    if args.skip_sandbox:
        print("      --skip-sandbox 지정으로 건너뜁니다.")

    log(3, "오프라인 pip Wheel 패키지 수집 중...")
    collect_wheels(sandbox_source)

    log(4, "포터블 파이썬 런타임 복제 중...")
    stage_python_runtime()

    has_node = False
    if args.skip_node:
        log(5, "Node 런타임 — --skip-node 지정으로 건너뜁니다.")
        log(6, "Node MCP 서버 — --skip-node 지정으로 건너뜁니다.")
    else:
        log(5, "Node 런타임(node.exe) 준비 중...")
        node_ok = stage_node_runtime(args.node_version)
        log(6, "공식 Node MCP 서버 설치 중 (filesystem / memory / sequential-thinking)...")
        has_node = stage_node_mcp_servers() and node_ok

    log(7, "폐쇄망 전용 실행 스크립트 및 문서 작성 중 (UTF-8 BOM)...")
    write_launchers(has_node=has_node, has_sandbox=sandbox_source is not None)

    log(8, f"최종 ZIP 아카이브 생성 중 ({ZIP_FILE})...")
    if ZIP_FILE.exists():
        ZIP_FILE.unlink()
    shutil.make_archive(str(DIST_DIR / "MultiAgentOrchestrator_offline"), "zip", STAGING_DIR)

    zip_size_mb = round(ZIP_FILE.stat().st_size / (1024 * 1024), 2)

    print("=" * 60)
    print(" [완료] 폐쇄망 패키징이 성공적으로 생성되었습니다!")
    print(f"  - 아카이브 경로: {ZIP_FILE}")
    print(f"  - 파일 크기: {zip_size_mb} MB")
    print("  - 포함 구성: 소스코드, conf.toml, 포터블 Python 런타임, 오프라인 Wheels,")
    print(f"               Node 런타임 + Node MCP 서버({'포함' if has_node else '미포함'}),")
    print(f"               코드 실행 샌드박스({'포함' if sandbox_source is not None else '미포함'}),")
    print("               실행 스크립트")
    print("=" * 60)


if __name__ == "__main__":
    main()

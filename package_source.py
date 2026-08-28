"""소스·설정만 담는 갱신용 패키지 스크립트.

`package_offline.py` 가 만드는 전체 번들은 포터블 파이썬, node.exe, wheel,
MCP 서버 설치본까지 들고 있어 수백 MB 입니다. 그 런타임은 한 번 반입하면
버전을 올릴 때까지 그대로 쓰면 됩니다. 코드만 고쳤을 때 그걸 매번 다시
반입하는 것은 용량도 용량이지만, 반입 심사를 매번 처음부터 다시 받는 일입니다.

이 스크립트는 **런타임을 제외한** 소스와 설정만 묶습니다.

    dist/MultiAgentOrchestrator_source_YYYYMMDD.zip
    └── MultiAgentOrchestrator_source/
        ├── app/                  애플리케이션 소스 (통째로 교체)
        ├── tests/                테스트 (--no-tests 로 제외)
        ├── wiki/                 문서 (--no-docs 로 제외)
        ├── conf.example.toml     설정 템플릿
        ├── conf.toml.new         현재 설정 (참고용. 대상의 conf.toml 을 덮지 않음)
        ├── .env.example
        ├── requirements.txt
        ├── setup_mcp.py, package_offline.py|ps1
        ├── README.md, CLAUDE.md
        ├── MANIFEST.txt          파일별 SHA-256 (반입 심사·무결성 확인용)
        ├── apply_update.ps1      기존 설치본에 덮어쓰는 스크립트
        └── README_SOURCE.md      반입 후 절차

사용법:
    python package_source.py [--no-tests] [--no-docs] [--out-dir dist]
                             [--max-file-mb 2] [--allow-secrets]

포함 목록은 **허용 목록(allow-list)** 입니다. 제외 목록으로 짜면 새 디렉터리가
생겼을 때 조용히 딸려 들어갑니다. 여기서는 새 디렉터리가 생기면 그냥 빠지고,
빠진 것은 눈에 띕니다.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
PACKAGE_NAME = "MultiAgentOrchestrator_source"

# --- 무엇을 담는가 (허용 목록) -------------------------------------------------

# mcp_servers/ 는 포크한 MCP 서버 원본입니다 (실행 사본은 앱이 mcp_node 에 놓습니다).
SOURCE_DIRS = ["app", "mcp_servers"]
TEST_DIRS = ["tests"]
DOC_DIRS = ["wiki"]

# (원본 이름, 패키지 안에서의 이름). 이름을 바꾸는 것은 conf.toml 하나뿐입니다.
ROOT_FILES: list[tuple[str, str]] = [
    ("requirements.txt", "requirements.txt"),
    ("conf.example.toml", "conf.example.toml"),
    (".env.example", ".env.example"),
    ("setup_mcp.py", "setup_mcp.py"),
    ("package_offline.py", "package_offline.py"),
    ("package_offline.ps1", "package_offline.ps1"),
    ("package_source.py", "package_source.py"),
    ("package_source.ps1", "package_source.ps1"),
    ("README.md", "README.md"),
    ("CLAUDE.md", "CLAUDE.md"),
    # 대상 장비의 conf.toml 에는 그 망의 실제 엔드포인트가 들어 있습니다.
    # 덮어쓰면 안 되므로 다른 이름으로 넣고, 운영자가 직접 비교하게 합니다.
    ("conf.toml", "conf.toml.new"),
]

# 디렉터리를 복사할 때 건너뛸 것들. 소스 트리 안에 런타임 부스러기가 섞이는 것을 막습니다.
IGNORE_PATTERNS = [
    "__pycache__", "*.pyc", "*.pyo", "*.egg-info",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "*.log", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".DS_Store", "Thumbs.db",
]

# 절대 들어가면 안 되는 것. 실수로 SOURCE_DIRS 에 추가되더라도 여기서 걸립니다.
FORBIDDEN_NAMES = {
    "python_runtime", "node_runtime", "wheels", "wheelhouse", "vendor",
    "mcp_node", "mcp_sandbox", "workspace", "dist", "build",
    ".git", ".venv", "venv", "env", "node_modules",
}

# 반입 심사 전에 걸러야 할 것들. conf.toml 은 gitignore 대상이라
# 누군가 실제 키를 적어 두었을 수 있습니다.
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "OpenAI 계열 API 키"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API 키"),
    (re.compile(r"AIza[A-Za-z0-9_\-]{30,}"), "Google API 키"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub 토큰"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack 토큰"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "개인 키"),
]

TEXT_SUFFIXES = {".py", ".toml", ".md", ".txt", ".ps1", ".bat", ".json", ".yml", ".yaml", ".example", ".svg",
                 ".js", ".mjs"}


def log(message: str) -> None:
    print(message, flush=True)


def is_ignored(path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, pat) for pat in IGNORE_PATTERNS)


def collect_dir(src: Path, rel_root: str) -> list[tuple[Path, str]]:
    """디렉터리 하나에서 담을 파일을 모읍니다. (원본 경로, 패키지 내 상대경로)"""
    collected: list[tuple[Path, str]] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(src).parts
        if any(p in FORBIDDEN_NAMES for p in parts) or any(is_ignored(Path(p)) for p in parts):
            continue
        if is_ignored(path):
            continue
        collected.append((path, f"{rel_root}/{path.relative_to(src).as_posix()}"))
    return collected


def scan_for_secrets(items: list[tuple[Path, str]]) -> list[str]:
    """반입 전에 걸러야 할 값이 섞여 있는지 봅니다."""
    findings: list[str] = []
    for src, rel in items:
        if src.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                findings.append(f"{rel}:{line}  {label} 로 보이는 값")
    return findings


def build_manifest(items: list[tuple[Path, str]]) -> str:
    """파일별 SHA-256. 반입 심사 기록과 반입 후 무결성 확인에 씁니다."""
    lines = [
        f"# {PACKAGE_NAME}",
        f"# generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"# files: {len(items)}",
        f"# bytes: {sum(src.stat().st_size for src, _ in items)}",
        "#",
        "# sha256                                                            size  path",
    ]
    for src, rel in sorted(items, key=lambda it: it[1]):
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        lines.append(f"{digest}  {src.stat().st_size:>9}  {rel}")
    return "\n".join(lines) + "\n"


APPLY_UPDATE_PS1 = """# 소스 갱신 적용 스크립트 (폐쇄망 설치본 위에서 실행)
#
#   .\\apply_update.ps1 -Target "C:\\path\\to\\MultiAgentOrchestrator_bundle"
#
# app/ 과 mcp_servers/ 는 통째로 교체합니다. 파일 단위로 덮어쓰면 이번 갱신에서
# 삭제된 파일이 대상에 남아 import 되기 때문입니다. 교체 전에 백업을 뜹니다.
# conf.toml 은 건드리지 않습니다. 이 망의 엔드포인트 설정이 들어 있습니다.

param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$NoBackup
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Source = $PSScriptRoot
if (-not $Source) { $Source = (Get-Location).Path }

if (-not (Test-Path (Join-Path $Target "app"))) {
    throw "대상에 app 폴더가 없습니다. 설치본 경로가 맞습니까? -> $Target"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

# 1. 백업
if (-not $NoBackup) {
    $backup = Join-Path $Target "_backup_$stamp"
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    foreach ($d in @("app", "mcp_servers")) {
        $p = Join-Path $Target $d
        if (Test-Path $p) { Copy-Item $p $backup -Recurse -Force }
    }
    foreach ($f in @("conf.toml", "requirements.txt")) {
        $p = Join-Path $Target $f
        if (Test-Path $p) { Copy-Item $p $backup -Force }
    }
    Write-Host "백업 완료: $backup"
}

# 2. app/ · mcp_servers/ 통째 교체
# mcp_servers/ 는 포크한 MCP 서버 원본입니다. 실행 사본(mcp_node/memory-scoped.mjs)은
# 앱이 기동할 때 여기서 다시 복사하므로, 이 폴더만 갱신되면 됩니다.
foreach ($d in @("app", "mcp_servers")) {
    $src = Join-Path $Source $d
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $Target $d
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
    Copy-Item $src $Target -Recurse -Force
    Write-Host "$d/ 교체 완료"
}

# 3. 설정이 아닌 부수 파일만 덮어쓰기
foreach ($f in @("requirements.txt", "conf.example.toml", ".env.example", "README.md")) {
    $src = Join-Path $Source $f
    if (Test-Path $src) { Copy-Item $src $Target -Force }
}

# 4. conf.toml 은 덮지 않고 비교만 안내
$confNew = Join-Path $Source "conf.toml.new"
$confCur = Join-Path $Target "conf.toml"
if ((Test-Path $confNew) -and (Test-Path $confCur)) {
    Copy-Item $confNew (Join-Path $Target "conf.toml.new") -Force
    $diff = Compare-Object (Get-Content $confCur) (Get-Content $confNew)
    if ($diff) {
        Write-Host ""
        Write-Host "conf.toml 이 다릅니다. 덮어쓰지 않았습니다." -ForegroundColor Yellow
        Write-Host "  현재: $confCur"
        Write-Host "  신규: $(Join-Path $Target 'conf.toml.new')"
        Write-Host "  비교: Compare-Object (Get-Content conf.toml) (Get-Content conf.toml.new)"
    }
}

Write-Host ""
Write-Host "갱신 완료. run_offline.bat 으로 실행하세요." -ForegroundColor Green
"""


README_SOURCE_MD = """# 소스 갱신 패키지 (런타임 미포함)

이 압축본에는 **애플리케이션 소스와 설정 템플릿만** 들어 있습니다.
포터블 파이썬, node.exe, pip wheel, MCP 서버 설치본은 들어 있지 않습니다.
이미 반입된 `MultiAgentOrchestrator_bundle` 위에 덮어쓰는 용도입니다.

## 적용 절차

```powershell
# 1. 압축을 풀고 그 폴더에서
.\\apply_update.ps1 -Target "C:\\Apps\\MultiAgentOrchestrator_bundle"

# 2. 실행
cd C:\\Apps\\MultiAgentOrchestrator_bundle
.\\run_offline.bat
```

`apply_update.ps1` 이 하는 일:

1. 대상의 `app/`, `conf.toml`, `requirements.txt` 를 `_backup_<시각>/` 에 백업합니다.
2. `app/` 을 **통째로 교체**합니다. 파일 단위로 덮어쓰면 이번 갱신에서 삭제된
   모듈이 대상에 남아 계속 import 되기 때문입니다.
3. `requirements.txt`, `conf.example.toml`, `.env.example`, `README.md` 를 덮어씁니다.
4. `conf.toml` 은 **건드리지 않습니다.** 이 망의 실제 엔드포인트가 들어 있습니다.
   대신 `conf.toml.new` 를 옆에 놓고 차이가 있으면 알려줍니다.

## 손으로 할 때

압축을 푼 뒤 대상 설치본에서:

| 원본 | 대상 | 방법 |
|---|---|---|
| `app/` | `app/` | 기존 폴더를 지우고 통째로 복사 |
| `requirements.txt` | 같은 이름 | 덮어쓰기 |
| `conf.example.toml` | 같은 이름 | 덮어쓰기 |
| `.env.example` | 같은 이름 | 덮어쓰기 |
| `conf.toml.new` | — | 대상 `conf.toml` 과 비교만. 덮어쓰지 말 것 |
| `wiki/`, `tests/` | 선택 | 참고용 |

건드리지 말아야 할 것: `python_runtime/`, `node_runtime/`, `wheels/`,
`mcp_node/`, `mcp_sandbox/`, `workspace/`, `multiagent.db`.

## 의존성이 바뀌었는지 확인

`requirements.txt` 가 이전 반입본과 다르면 런타임에 없는 패키지가 생긴 것이므로
소스만 갱신해서는 실행되지 않습니다. 그때는 전체 번들을 다시 반입해야 합니다.

```powershell
Compare-Object (Get-Content _backup_*\\requirements.txt) (Get-Content requirements.txt)
```

차이가 없으면 소스 갱신만으로 충분합니다.

## DB 스키마

`multiagent.db` 는 그대로 두십시오. 기동 시 `init_db()` 가 빠진 컬럼만 채워 넣습니다.
지난 세션 기록과 산출물은 보존됩니다.

## 무결성 확인

`MANIFEST.txt` 에 파일별 SHA-256 이 있습니다.

```powershell
Get-Content MANIFEST.txt | Where-Object { $_ -notmatch '^#' } | ForEach-Object {
    $parts = $_ -split '\\s+', 3
    $actual = (Get-FileHash $parts[2] -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $parts[0]) { Write-Host "불일치: $($parts[2])" -ForegroundColor Red }
}
```
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="런타임을 뺀 소스·설정만 dist/ 에 압축합니다."
    )
    parser.add_argument("--out-dir", default="dist", help="산출물 위치 (기본: dist)")
    parser.add_argument("--no-tests", action="store_true", help="tests/ 제외")
    parser.add_argument("--no-docs", action="store_true", help="wiki/ 및 CLAUDE.md 제외")
    parser.add_argument("--no-conf", action="store_true", help="conf.toml.new 를 넣지 않음")
    parser.add_argument("--max-file-mb", type=float, default=2.0,
                        help="이보다 큰 파일이 있으면 중단 (런타임 혼입 방지, 기본: 2MB)")
    parser.add_argument("--allow-secrets", action="store_true",
                        help="키처럼 보이는 값이 있어도 강행")
    args = parser.parse_args()

    dist_dir = (ROOT_DIR / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() \
        else Path(args.out_dir)
    staging = dist_dir / PACKAGE_NAME
    zip_path = dist_dir / f"{PACKAGE_NAME}_{datetime.now():%Y%m%d}.zip"

    # --- 1. 담을 것 모으기 ---------------------------------------------------
    dirs = list(SOURCE_DIRS)
    if not args.no_tests:
        dirs += TEST_DIRS
    if not args.no_docs:
        dirs += DOC_DIRS

    items: list[tuple[Path, str]] = []
    for name in dirs:
        src = ROOT_DIR / name
        if not src.is_dir():
            log(f"  [건너뜀] {name}/ 이 없습니다.")
            continue
        found = collect_dir(src, name)
        items.extend(found)
        log(f"  {name}/  {len(found)}개")

    for src_name, dest_name in ROOT_FILES:
        if dest_name == "conf.toml.new" and args.no_conf:
            continue
        if args.no_docs and src_name == "CLAUDE.md":
            continue
        src = ROOT_DIR / src_name
        if not src.is_file():
            log(f"  [건너뜀] {src_name} 이 없습니다.")
            continue
        items.append((src, dest_name))
    log(f"  루트 파일  {sum(1 for _, r in items if '/' not in r)}개")

    if not items:
        sys.exit("담을 파일이 없습니다. 프로젝트 루트에서 실행하고 있습니까?")

    # --- 2. 검사 -------------------------------------------------------------
    limit = int(args.max_file_mb * 1024 * 1024)
    oversized = [(rel, src.stat().st_size) for src, rel in items if src.stat().st_size > limit]
    if oversized:
        log(f"\n[중단] 상한({args.max_file_mb} MB)을 넘는 파일이 있습니다:")
        for rel, size in sorted(oversized, key=lambda it: -it[1])[:20]:
            unit = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"
            log(f"  {rel}  ({unit})")
        if len(oversized) > 20:
            log(f"  ... 외 {len(oversized) - 20}개")
        sys.exit("런타임 파일이 섞였는지 확인하거나 --max-file-mb 를 올리세요.")

    findings = scan_for_secrets(items)
    if findings:
        log("\n[경고] 키로 보이는 값이 있습니다. 폐쇄망 반입 심사에서 문제가 됩니다:")
        for f in findings:
            log(f"  {f}")
        if not args.allow_secrets:
            sys.exit("해당 값을 ${ENV_VAR} 로 바꾸거나, 알고 있다면 --allow-secrets 로 실행하세요.")
        log("  --allow-secrets 로 강행합니다.")

    # --- 3. 스테이징 ---------------------------------------------------------
    if staging.exists():
        shutil.rmtree(staging)
    for src, rel in items:
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    (staging / "apply_update.ps1").write_text(APPLY_UPDATE_PS1, encoding="utf-8-sig", newline="\r\n")
    (staging / "README_SOURCE.md").write_text(README_SOURCE_MD, encoding="utf-8")
    (staging / "MANIFEST.txt").write_text(build_manifest(items), encoding="utf-8")

    # --- 4. 압축 -------------------------------------------------------------
    dist_dir.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, f"{PACKAGE_NAME}/{path.relative_to(staging).as_posix()}")

    total = sum(src.stat().st_size for src, _ in items)
    log("")
    log(f"  스테이징 : {staging}")
    log(f"  압축     : {zip_path}")
    log(f"  파일     : {len(items) + 3}개 (원본 {len(items)} + 생성 3)")
    log(f"  원본 크기: {total / 1024:.0f} KB  ->  압축 {zip_path.stat().st_size / 1024:.0f} KB")
    log("")
    log("  런타임(python_runtime, node_runtime, wheels, mcp_node, mcp_sandbox)과")
    log("  운영 데이터(workspace, multiagent.db)는 들어 있지 않습니다.")
    log("  대상 장비에서: .\\apply_update.ps1 -Target <설치본 경로>")


if __name__ == "__main__":
    main()

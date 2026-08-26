"""개발 PC용 MCP 서버 일괄 설치 스크립트.

`conf.toml` 이 기본으로 켜 두는 MCP 서버들이 바로 붙도록 준비합니다.
폐쇄망 배포본은 `package_offline.py` 가 같은 구성을 번들에 넣으므로 이 스크립트가
필요 없습니다.

    python setup_mcp.py [--skip-node] [--skip-sandbox]

수행 항목:
  1. ./workspace 생성 및 git 저장소 초기화
     (git MCP 서버는 유효한 git 저장소가 아니면 기동에 실패합니다)
  2. ./mcp_node 에 공식 Node MCP 서버 설치 (filesystem / memory / sequential-thinking)
  3. ./mcp_sandbox 에 AirgappedPySandbox 체크아웃 (코드 실행 샌드박스)

파이썬 MCP 서버(mcp-server-git 등)는 requirements.txt 에 포함되어 있으므로
`pip install -r requirements.txt` 로 이미 설치됩니다.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

NODE_MCP_PACKAGES = [
    "@modelcontextprotocol/server-filesystem",
    "@modelcontextprotocol/server-memory",
    "@modelcontextprotocol/server-sequential-thinking",
]
SANDBOX_REPO_URL = "https://github.com/HaJaehee/AirgappedPySandbox"


def _run(cmd: list[str], **kwargs) -> bool:
    try:
        subprocess.run(cmd, check=True, **kwargs)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"      [실패] {' '.join(cmd[:3])}...: {exc}")
        return False


def setup_workspace() -> bool:
    """에이전트 공용 작업 공간을 만들고 git 저장소로 초기화합니다."""
    workspace = ROOT_DIR / "workspace"
    workspace.mkdir(exist_ok=True)

    if (workspace / ".git").exists():
        print("      이미 git 저장소입니다.")
        return True

    if shutil.which("git") is None:
        print("      [경고] git 을 찾을 수 없습니다. git MCP 서버는 기동에 실패합니다.")
        return False

    if not _run(["git", "init", "-q", str(workspace)]):
        return False

    # 빈 저장소에서도 git_log 등이 동작하도록 최초 커밋을 남깁니다.
    keep = workspace / ".gitkeep"
    keep.touch()
    _run(["git", "-C", str(workspace), "add", ".gitkeep"])
    _run(
        ["git", "-C", str(workspace), "-c", "user.name=multiagent",
         "-c", "user.email=multiagent@localhost", "commit", "-q", "-m", "workspace 초기화"],
    )
    print(f"      git 저장소로 초기화했습니다: {workspace}")
    return True


def setup_node_servers() -> bool:
    """공식 Node MCP 서버를 ./mcp_node 에 설치합니다."""
    target = ROOT_DIR / "mcp_node"
    if (target / "node_modules" / "@modelcontextprotocol").exists():
        print("      이미 설치되어 있습니다.")
        return True

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        print("      [경고] npm 을 찾을 수 없습니다. Node.js LTS 설치 후 다시 실행하세요.")
        print("             filesystem / memory MCP 서버 없이 동작하게 됩니다.")
        return False

    target.mkdir(exist_ok=True)
    print(f"      설치 중: {', '.join(p.rsplit('/', 1)[-1] for p in NODE_MCP_PACKAGES)}")
    ok = _run([npm, "install", "--omit=dev", "--no-audit", "--no-fund",
               "--prefix", str(target), *NODE_MCP_PACKAGES])
    if ok:
        bin_dir = target / "node_modules" / ".bin"
        if bin_dir.exists():
            shutil.rmtree(bin_dir, ignore_errors=True)
    return ok


def setup_sandbox() -> bool:
    """코드 실행 샌드박스를 ./mcp_sandbox 에 준비합니다."""
    target = ROOT_DIR / "mcp_sandbox"
    if (target / "server.py").exists():
        print("      이미 준비되어 있습니다.")
        return True

    if shutil.which("git") is None:
        print("      [경고] git 을 찾을 수 없어 샌드박스를 받을 수 없습니다.")
        return False

    print(f"      클론 중: {SANDBOX_REPO_URL}")
    if not _run(["git", "clone", "--depth", "1", SANDBOX_REPO_URL, str(target)]):
        print("             수동으로 받으려면:")
        print(f"               git clone --depth 1 {SANDBOX_REPO_URL} ./mcp_sandbox")
        return False

    (target / "workspace").mkdir(exist_ok=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="개발 PC용 MCP 서버 설치")
    parser.add_argument("--skip-node", action="store_true", help="Node MCP 서버 설치 건너뛰기")
    parser.add_argument("--skip-sandbox", action="store_true", help="코드 실행 샌드박스 건너뛰기")
    args = parser.parse_args()

    print("=" * 60)
    print("  MCP 서버 설치 (개발 PC용)")
    print("=" * 60)

    results: dict[str, bool] = {}

    print("[1/3] 작업 공간(./workspace) 준비 중...")
    results["workspace"] = setup_workspace()

    if args.skip_node:
        print("[2/3] Node MCP 서버 — 건너뜁니다.")
    else:
        print("[2/3] 공식 Node MCP 서버(./mcp_node) 설치 중...")
        results["mcp_node"] = setup_node_servers()

    if args.skip_sandbox:
        print("[3/3] 코드 실행 샌드박스 — 건너뜁니다.")
    else:
        print("[3/3] 코드 실행 샌드박스(./mcp_sandbox) 준비 중...")
        results["mcp_sandbox"] = setup_sandbox()

    print("=" * 60)
    for name, ok in results.items():
        print(f"  {'OK  ' if ok else '실패'}  {name}")
    print()
    print("  파이썬 MCP 서버(mcp-server-git 등)는 requirements.txt 에 있습니다:")
    print("      pip install -r requirements.txt")
    print()
    print("  서버를 띄운 뒤 로스터 패널의 'MCP 서버' 칩에서 연결 상태를 확인하세요.")
    print("=" * 60)

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()

# MCP 호스트

> 상위: [핵심 기술 개관](README.md) · 이전: [LLM 통합](03-llm-integration.md) · 다음: [오케스트레이션 엔진](05-orchestration-engine.md)
>
> 파일: `app/mcp/manager.py` (431줄) · `app/mcp/client.py` (561줄)

**Model Context Protocol** 은 LLM 에게 도구를 제공하는 표준입니다. 이 앱은 MCP
**호스트**로서, 외부 도구 서버를 자식 프로세스로 띄우고 stdio 로 통신합니다.

---

## 왜 프로세스를 분리하는가

에이전트가 파일을 읽고 코드를 실행하는 능력을 앱 안에 구현하면, 그 기능이 앱과
같은 권한으로 돕니다. 별도 프로세스면:

- filesystem 서버가 **지정된 디렉터리 밖 경로를 스스로 차단**합니다
- 샌드박스 커널이 죽어도 앱은 살아 있습니다
- 도구를 추가하는 일이 설정에 실행 명령 한 줄을 적는 것입니다
- 서버가 어느 언어로 쓰였든 상관없습니다 (Node / Python 혼재)

---

## 두 개의 층

```text
MCPManager  (프로세스 전체 싱글턴)
  │  서버 수명 관리 · 도구 이름 색인 · 작업 공간 전환
  │
  ├── MCPClientConnection("filesystem")  ── stdio ──▶ node .../server-filesystem
  ├── MCPClientConnection("memory")      ── stdio ──▶ node memory-scoped.mjs
  ├── MCPClientConnection("git")         ── stdio ──▶ python -m mcp_server_git
  └── MCPClientConnection("sandbox")     ── stdio ──▶ python .../server.py
```

`MCPClientConnection` 은 서버 하나와의 세션을 담당합니다: 연결, 도구 목록 조회,
호출, 재연결, 종료, 그리고 자식의 stderr 를 갈무리해 두는 일.

---

## 기본 구성 서버

| 이름 | 실행 | 도구 수 | 하는 일 |
| :--- | :--- | :--- | :--- |
| `filesystem` | node (공식) | 14 | 작업 공간 파일 I/O. 지정 디렉터리 밖 차단 |
| `memory` | node (**포크**) | 9 | 지식 그래프. 합의된 사실 축적 |
| `git` | python | 12 | 산출물 버전 관리. 라운드별 diff 추적 |
| `sandbox` | python | 5 | 상태 유지형 Python 실행. 코드 주장을 실제로 판정 |
| `sequential_thinking` | node (공식) | 1 | `mcp` 모드에서만 |
| `fetch` | python | 1 | URL → 마크다운. **폐쇄망에서는 끄세요** |

### memory 서버를 포크한 이유

공식 memory 서버는 **프로세스 하나에 그래프 파일 하나**입니다. 서버를 공유하는
다음 대화가 이전 대화의 기억을 그대로 읽습니다. `mcp_servers/memory_scoped/`
포크는 그래프를 대화별 파일로 나누고, 어느 그래프를 열지는 앱이 매 호출의 요청
메타데이터로 알려줍니다.

### 샌드박스 커널 격리

커널 네임스페이스는 **대화 × 발언자** 단위로 갈립니다. 커널 안의 변수는 어디에도
기록되지 않아서(다음 발언자의 컨텍스트에는 앞 발언의 본문만 들어갑니다),
에이전트 사이에 넘기면 아무도 검증할 수 없는 상태가 생기기 때문입니다.
인계는 작업 공간 파일로 합니다.

필요한 커널 수 = **동시 토론 수 × 샌드박스를 쓰는 에이전트 수**. 기본 구성은
coder·critic 둘이므로 16이면 동시 토론 8건까지 버팁니다. 넘으면 가장 오래 안 쓴
커널부터 정리되어 그 변수들이 사라집니다.

---

## 스코프

도구 호출마다 **어느 대화의, 누구의** 호출인지가 함께 실립니다.

```python
AGENT_SCOPED_SERVERS = frozenset({"sandbox"})

def compose_scope(server_name, scope, actor):
    if not scope:
        return None
    if server_name in AGENT_SCOPED_SERVERS and actor:
        return f"{scope}-{actor}"     # 샌드박스: 대화 × 발언자
    return scope                      # 그 외: 대화 단위
```

| 서버 | 스코프 단위 | 이유 |
| :--- | :--- | :--- |
| `memory` | 대화 | 합의된 사실은 그 대화의 것 |
| `sandbox` | 대화 × 발언자 | 커널 변수는 넘길 수 없음 |
| `filesystem`, `git` | 작업 공간 (스코프 무관) | 파일은 공유하는 것이 목적 |

이것을 빠뜨리면 서버가 대화를 구분하지 못해 **다른 대화의 지식 그래프를 읽습니다.**

---

## 도구 이름

여러 서버가 같은 이름의 도구를 낼 수 있으므로 `서버명__도구명` 으로 한정합니다.

```text
filesystem__read_text_file
sandbox__execute_python_code
git__git_diff
```

`execute_tool()` 은 한정 이름과 평이한 이름을 모두 받습니다. 색인에 없으면
`__` 로 잘라 서버를 찾고, 그래도 없으면 **사용 가능한 도구 목록과 함께** 오류를
돌려줍니다 — 모델이 그것을 읽고 스스로 교정합니다.

---

## 실패를 그대로 전달

```python
except MCPToolError as e:
    return e.message, "error"      # 서버가 보고한 메시지 그대로
except Exception as e:
    return f"Tool execution failed ({type(e).__name__}): {e}", "error"
```

도구 실패는 **예외로 올리지 않고 결과 문자열로 돌려줍니다.** 모델이 그 메시지를
읽고 인자를 고쳐 다시 시도할 수 있어야 하기 때문입니다. 파일이 없다는 오류는
정보이지 사고가 아닙니다.

`_StderrTee` 가 자식 프로세스의 stderr 앞 4줄·뒤 8줄을 갈무리해 두어,
연결 실패 시 화면 상태 칩의 툴팁에 실제 원인이 뜹니다.

```text
Cannot find package '@modelcontextprotocol/sdk' imported from .../memory-scoped.mjs
```

---

## 작업 공간 전환

filesystem 은 허용 디렉터리를 **argv** 로, sandbox 는 `SANDBOX_WORKSPACE` 를
**env** 로 받습니다. 둘 다 기동 시점에 고정되므로, 경로가 달라지면 **서버를 다시
띄우는 것 외에 방법이 없습니다.**

```python
async def set_workspace(self, path):
    target = resolve_workspace_dir(str(path))
    if self._initialized and target == self.workspace:
        return target
    ensure_workspace(str(target))
    # 이미 치환된 문자열을 찾아 바꾸는 대신, 원문을 새 WORKSPACE_DIR 로 다시 풉니다.
    self.server_configs = get_config().mcp_servers_for_workspace(target)
    self._workspace = target
    await self.initialize()
```

`${WORKSPACE_DIR}` 가 어디에 몇 번 나오든 정확합니다 — 같은 치환기를 한 번 더
돌리는 것이니까요. **`conf.json` 은 건드리지 않습니다.** 작업 공간은 대화의
설정이지 배포 설정이 아닙니다.

MCP 서버는 프로세스 전체가 공유하므로, **서로 다른 작업 공간의 토론을 동시에
돌리는 것은 거절**됩니다 (`WorkspaceConflictError`).

---

## 연결 상태

```python
manager.connection_status()
# {"filesystem": {"connected": True, "available": True, "tool_count": 14, "error": None},
#  "memory":     {"connected": False, ..., "error": "Cannot find package ..."}}
```

| 칩 | 뜻 |
| :--- | :--- |
| 🟢 연결됨 | 세션 수립, 도구 사용 가능 |
| 🔴 실패 | 기동/연결 실패. 툴팁에 stderr |
| ⚪ 비활성 | `conf.json` 에서 `"enabled": false` |

**연결 실패는 앱을 막지 않습니다.** 그 도구를 쓰려던 에이전트는 도구 없이
발언합니다.

---

## MCP Roots

앱은 `roots/list` 요청에 작업 공간 경로를 응답합니다. 서버가 "내가 다뤄도 되는
루트가 어디인가" 를 물어볼 수 있게 하는 표준 기능입니다.

---

## 서버 추가하기

화면에서(로스터 → MCP 서버 추가) 또는 `conf.json` 에 직접:

```json
"everything": {
  "command": "${PYTHON_BIN:-python}",
  "args": ["-m", "server_everything"],
  "env": { "API_KEY": "${SOME_API_KEY}" },
  "enabled": true
}
```

추가만으로는 아무도 쓰지 않습니다. 쓸 에이전트의 `allowed_mcp_servers` 에 이름을
넣어야 합니다. → [로스터 편집](../04-workflows/03-roster-editing.md)

> 폐쇄망에서 `npx` 를 쓰지 마세요. 패키지가 로컬에 없으면 npm 레지스트리에
> 접속합니다. 진입점을 `node` 로 직접 실행하세요.

---

## 관련 문서

- [LLM 통합](03-llm-integration.md) — 도구 루프를 도는 쪽
- [conf.json 설정](../02-getting-started/02-configuration.md#mcp_servers--도구-서버) — 설정 항목
- [로스터 편집](../04-workflows/03-roster-editing.md) — 화면에서 서버 관리
- [폐쇄망 배포](../04-workflows/05-airgap-deployment.md) — MCP 서버 번들링

---

> 다음: [오케스트레이션 엔진](05-orchestration-engine.md)

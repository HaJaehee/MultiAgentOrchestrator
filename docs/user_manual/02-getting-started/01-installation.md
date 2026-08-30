# 설치와 첫 실행

> 상위: [시작하기](README.md) · 다음: [conf.json 설정](02-configuration.md)

---

## 사전 요구사항

| 항목 | 필요 여부 | 비고 |
| :--- | :--- | :--- |
| Python 3.11+ | **필수** | `tomllib` 이후 세대. async TaskGroup 사용 |
| Node.js 18+ | 선택 | Node MCP 서버 3종을 쓸 때만. 설치 시점에만 필요 |
| git | 권장 | git MCP 서버, `workspace` 저장소 초기화 |
| 인터넷 | 최초 1회 | 의존성·MCP 서버 내려받기 |

---

## 1. 파이썬 의존성

```bash
pip install -r requirements.txt
```

가상환경을 쓰는 것을 권합니다. MCP 서버 중 파이썬으로 도는 것들
(`git`, `sandbox`, `fetch`)은 **앱과 같은 인터프리터**로 기동되기 때문입니다.
`app/config.py` 가 `PYTHON_BIN` 을 `sys.executable` 로 자동 설정하므로,
가상환경 안에서 앱을 띄우면 MCP 서버도 그 환경에서 뜹니다.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate      # macOS / Linux
pip install -r requirements.txt
```

---

## 2. MCP 서버 준비

```bash
python setup_mcp.py
```

하는 일:

| 대상 | 내용 |
| :--- | :--- |
| `./workspace` | 폴더 생성 + **git 저장소 초기화**. git MCP 서버는 유효한 저장소가 아니면 기동에 실패합니다 |
| `./mcp_node` | 공식 Node MCP 서버 설치 (filesystem / memory / sequential-thinking) |
| `./mcp_node/memory-scoped.mjs` | 포크한 memory 서버 실행 사본 배치 (대화별 지식 그래프) |
| `./mcp_sandbox` | [AirgappedPySandbox](https://github.com/HaJaehee/AirgappedPySandbox) 체크아웃 |

옵션:

```bash
python setup_mcp.py --skip-node      # Node 서버 건너뛰기
python setup_mcp.py --skip-sandbox   # 코드 실행 샌드박스 건너뛰기
```

건너뛴 서버는 `conf.json` 에서 꺼두세요. 켜 둔 채로 두면 기동할 때마다 실패
로그가 쌓입니다.

```json
"sandbox": { "command": "...", "args": ["..."], "enabled": false }
```

> `npx` 는 패키지가 로컬에 없으면 npm 레지스트리에 접속하므로 폐쇄망에서
> 동작하지 않습니다. 그래서 `conf.json` 은 진입점(`dist/index.js`)을 `node` 로
> 직접 실행합니다.

---

## 3. 설정 파일

```bash
cp conf.example.json conf.json
```

`conf.json` 은 `.gitignore` 대상입니다. 자세한 구조는
[conf.json 설정](02-configuration.md)을 보세요.

---

## 4. 환경 변수

```bash
cp .env.example .env
```

`conf.json` 의 모든 문자열 값이 `${VAR}` / `${VAR:-기본값}` 치환을 지원하므로,
엔드포인트와 키는 `.env` 에 두고 설정 파일은 그대로 공유하는 것이 기본 사용법입니다.

```dotenv
# 전역 LLM (모든 에이전트가 상속)
LLM_API_BASE=http://localhost:1234/v1
LLM_MODEL=openai/qwen2.5-coder-32b
LLM_API_KEY=

# 에이전트별로 다른 모델을 쓰고 싶다면
ORCHESTRATOR_MODEL=openai/gpt-4o
ARCHITECT_MODEL=anthropic/claude-3-5-sonnet-20241022
CODER_MODEL=openai/gpt-4o
CRITIC_MODEL=google/gemini-1.5-pro

# 서버 기동 (conf.json 의 app 이 참조)
APP_HOST=127.0.0.1
APP_PORT=8000
```

---

## 5. 실행

```bash
python -m app.main
```

기동 로그가 순서대로 나옵니다.

```text
Loaded configuration for host=127.0.0.1:8000, db=sqlite+aiosqlite:///./multiagent.db
Web UI: http://127.0.0.1:8000
SQLite database tables initialized.
MCP session established for 'filesystem'
Discovered 14 tools from MCP server 'filesystem'
...
MCPManager initialized. Connected: ['filesystem', 'memory', 'git', 'sandbox'] | Total registered tools: 39
Agent 'orchestrator' -> model=openai/gpt-4o, endpoint=http://localhost:1234/v1, sequential_thinking=on:prompt
AgentPool loaded 4 agents: ['orchestrator', 'architect', 'coder', 'critic']
Application startup complete.
```

`endpoint=no endpoint configured` 가 보이면 그 에이전트는 발언하지 못합니다.
`.env` 를 확인하세요.

### 실행 옵션

| 옵션 | 설명 |
| :--- | :--- |
| `--host HOST` | 바인딩 주소 (`conf.json` 과 `.env` 를 덮어씀) |
| `--port PORT` | 포트 |
| `--config PATH` | 설정 파일 경로 (기본 `conf.json`) |
| `--reload` / `--no-reload` | 자동 재시작 (기본값은 `conf.json` 의 `app.debug`) |

우선순위는 **CLI > `.env` > `conf.json` 기본값** 입니다.

```bash
python -m app.main --host 0.0.0.0 --port 9000 --config custom_conf.json
```

---

## 6. 확인

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/agents
curl http://127.0.0.1:8000/api/mcp
```

`/api/agents` 의 각 항목에 있는 `"mode": "live"` 가 실제 호출이 가능하다는
뜻입니다. `"unconfigured"` 면 엔드포인트도 키도 없는 상태입니다.
→ [HTTP API](../05-reference/01-http-api.md)

---

## 테스트 실행

```bash
pytest -q
```

→ [테스트](../05-reference/03-testing.md)

---

## 문제 해결

| 증상 | 원인과 조치 |
| :--- | :--- |
| `endpoint=no endpoint configured` | `.env` 의 `LLM_API_BASE` / `LLM_API_KEY` 미설정 |
| `Cannot find package '@modelcontextprotocol/sdk'` | `python setup_mcp.py` 미실행 (Node 서버 미설치) |
| `can't open file '.../mcp_sandbox/server.py'` | 샌드박스 미설치. `setup_mcp.py` 실행 또는 `"enabled": false` |
| git MCP 서버 기동 실패 | `workspace` 가 git 저장소가 아님. `git init workspace` |
| 설정 파일 문법 오류 | 오류 메시지에 **줄 번호와 칸**이 나옵니다. 그 위치를 보세요 |
| 다른 대화가 진행 중이라 시작 거부 | 작업 공간이 서로 다른 토론은 동시에 못 돕니다. [아키텍처](../01-overview/02-architecture.md#동시성-모델) |

---

> 다음: [conf.json 설정](02-configuration.md)

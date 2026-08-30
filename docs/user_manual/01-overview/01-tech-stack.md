# 기술 스택

> 상위: [시스템 개요](README.md) · 다음: [아키텍처](02-architecture.md)

전 구간이 **파이썬 하나**입니다. 프런트엔드 빌드 단계(Node 툴체인, 번들러,
별도 dev 서버)가 없고, `uvicorn` 프로세스 하나가 API 와 UI 를 함께 서빙합니다.
이것이 폐쇄망 배포를 현실적으로 만드는 가장 큰 요인입니다 — 반입할 것이
파이썬 런타임과 wheel 뿐입니다.

---

## 스택 한눈에

| 층 | 채택 기술 | 버전 | 역할 |
| :--- | :--- | :--- | :--- |
| 런타임 | Python | 3.11+ | async/await, TaskGroup, 강한 타이핑 |
| 웹 서버 | FastAPI + Uvicorn | 0.115+ / 0.30+ | 비동기 HTTP, 생명주기 관리, JSON API |
| UI | NiceGUI | 2.0+ | 파이썬만으로 반응형 SPA (Vue/Quasar 렌더링), WebSocket 실시간 갱신 |
| LLM 추상화 | LiteLLM | 1.40+ | 100+ 프로바이더를 OpenAI 호환 단일 인터페이스로 |
| 도구 프로토콜 | MCP Python SDK | 1.29+, <2 | 외부 도구 서버를 stdio 로 기동·호출 |
| 영속화 | SQLAlchemy 2.0 + aiosqlite | 2.0.30+ / 0.20+ | 비동기 ORM, 단일 파일 DB |
| 설정·검증 | Pydantic v2 + 표준 `json` | 2.8+ | 선언적 스키마 검증, 환경변수 치환 |
| 테스트 | pytest + pytest-asyncio | 8.0+ | 249개 테스트 |

---

## 왜 이것을 골랐는가

### FastAPI + NiceGUI — 파이썬 단일 프로세스

NiceGUI 는 내부적으로 Vue/Quasar 를 렌더링하지만, 개발자는 파이썬만 씁니다.
`ui.run_with(server, ...)` 로 기존 FastAPI 앱에 얹히므로 **API 와 UI 가 같은
포트, 같은 이벤트 루프**에서 돕니다.

```python
# app/main.py
server = FastAPI(lifespan=lifespan)   # /api/* 엔드포인트
create_ui()                            # NiceGUI 페이지 등록
ui.run_with(server, dark=True)         # 같은 앱에 마운트
```

얻는 것:

- 빌드 산출물이 없어 소스만 반입하면 갱신이 끝납니다
- 토론 진행 상황을 WebSocket 으로 밀어 넣는 코드가 그냥 파이썬 함수입니다
- 백엔드 객체(에이전트 풀, MCP 매니저)를 UI 에서 직접 참조합니다 — 직렬화 경계가 없습니다

### LiteLLM — 프로바이더를 설정값으로

에이전트마다 다른 모델을 쓰는 것이 이 시스템의 전제입니다. LiteLLM 은 OpenAI,
Anthropic, Google, Azure, Bedrock, Ollama, vLLM, LM Studio, 사내 게이트웨이를
`acompletion()` 하나로 덮습니다. 모델 교체가 **코드 변경이 아니라 문자열 변경**이
됩니다.

```json
"model": "openai/gpt-4o"
"model": "anthropic/claude-3-5-sonnet-20241022"
"model": "ollama_chat/qwen2.5-coder:14b"
```

`drop_params: true` 는 로컬 모델 호환성을 위한 안전장치입니다 — 엔드포인트가
모르는 파라미터를 조용히 제거해, `top_p` 를 안 받는 서버 때문에 요청 전체가
400 으로 죽는 일을 막습니다.

### MCP — 도구를 프로세스 경계 밖으로

에이전트가 파일을 읽고 코드를 실행하는 능력을 애플리케이션 안에 구현하면,
그 기능이 앱과 같은 권한으로 돕니다. MCP 서버는 **별도 프로세스**이고 stdio 로
통신하므로:

- filesystem 서버가 지정된 디렉터리 밖 경로를 스스로 차단합니다
- 샌드박스 서버의 커널이 죽어도 앱은 살아 있습니다
- 도구를 추가하는 일이 설정에 실행 명령 한 줄을 적는 것입니다

버전을 `mcp>=1.29.0,<2` 로 고정한 이유가 있습니다. mcp 2.x 는
`mcp.server.fastmcp` 를 제거했고(`MCPServer` 로 개명), 샌드박스 서버와
`mcp-server-git` 이 아직 1.x 계열을 요구합니다.

### SQLite + SQLAlchemy 2.0 — 파일 하나

토론 기록은 동시 쓰기가 거의 없고(대화 하나에 쓰는 주체는 하나), 반입 시
DB 파일 하나만 옮기면 됩니다. `aiosqlite` 로 비동기 드라이버를 쓰므로 이벤트
루프를 막지 않습니다. `db_url` 이 설정값이라 나중에 PostgreSQL 로 옮길 때
드라이버 문자열만 바꾸면 됩니다.

### Pydantic v2 + 표준 `json` — 설정을 읽고 **쓰기**

설정 파일이 JSON 인 것은 의도적인 선택입니다. 이 앱은 설정을 **읽기만 하지
않습니다** — 화면에서 에이전트를 추가하거나 발언 순서를 바꾸면 그 결과를 파일에
되씁니다. 표준 라이브러리에 JSON 기록기가 있으므로 그 코드가 딕셔너리 조작
몇 줄입니다.

> 예전에는 TOML 이었습니다. 파이썬 표준 라이브러리에 TOML *리더*는 있어도
> *라이터*가 없어서, 되쓰는 코드가 줄 단위 편집이었습니다 — 여러 줄 문자열
> 안인지 추적하고, 섹션 경계를 찾고, 값 종류마다 직렬화를 손으로 했습니다.
> 자세한 내용은 [설정 레이어](../03-core/01-config-layer.md).

---

## 의존성 목록

`requirements.txt` 전문:

```text
fastapi>=0.115.0
uvicorn>=0.30.0
nicegui>=2.0.0
pydantic>=2.8.0
pydantic-settings>=2.4.0
sqlalchemy>=2.0.30
aiosqlite>=0.20.0
litellm>=1.40.0
mcp>=1.29.0,<2

mcp-server-git>=2025.1.14   # git MCP 서버
jupyter_client>=8.0          # sandbox MCP 서버가 커널 구동에 사용
ipykernel>=6.0
# mcp-server-fetch           # 선택: 외부 네트워크가 열린 환경에서만

python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

**Node 는 런타임 의존성이 아닙니다.** 공식 MCP 서버 3종(filesystem, memory,
sequential-thinking)이 순수 JS 라 `node.exe` 하나면 돌아갑니다. npm/npx 는 설치
시점에만 필요하고, 폐쇄망 번들에는 포함되지 않습니다.

---

## 관련 문서

- [아키텍처](02-architecture.md) — 이 스택이 어떤 레이어로 나뉘는가
- [설정 레이어](../03-core/01-config-layer.md) — Pydantic + JSON 구현
- [LLM 통합](../03-core/03-llm-integration.md) — LiteLLM 사용 방식
- [MCP 호스트](../03-core/04-mcp-host.md) — MCP SDK 사용 방식
- [폐쇄망 배포](../04-workflows/05-airgap-deployment.md) — 이 스택을 통째로 옮기기

---

> 다음: [아키텍처](02-architecture.md)

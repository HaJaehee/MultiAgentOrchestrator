# [Project Specification] Python 기반 자율형 멀티 에이전트 협업 & 토론 플랫폼

당신은 최신 AI 시스템 아키텍처 및 풀스택 파이썬 개발 전문가입니다.
아래 명세와 아키텍처 요구사항에 따라 `conf.toml` 설정 기반의 **"MCP 도구를 활용하는 반응형 멀티 에이전트 토론 & 오케스트레이션 웹 애플리케이션"**을 구축해주세요.

---

## 1. 시스템 아키텍처 & 기술 스택

- **Backend**: Python 3.11+, **FastAPI** + **Uvicorn** (비동기 이벤트 루프 기반)
- **Frontend**: **NiceGUI** 또는 **Streamlit / FastHTML** (Python 반응형 UI, 실시간 WebSocket / SSE 지원)
- **Agent Orchestration**: **LangGraph** 또는 경량 커스텀 비동기 StateGraph 엔진 (오케스트레이터 중재 토론 그래프)
- **Tool Protocol**: **Model Context Protocol (MCP)** Python SDK (백엔드가 MCP Host/Client 역할 수행)
- **LLM Integration**: **LiteLLM** 또는 **LangChain / OpenAI SDK** 호환 인터페이스 (다양한 LLM Provider를 단일 인터페이스로 추상화)
- **Database & Storage**: **SQLite + SQLAlchemy/SQLModel** (세션, 대화 내역, 산출물 영구 저장)
- **Configuration**: **TOML** (`conf.toml` 파싱 및 유효성 검증 via Pydantic)

---

## 2. 세부 기능 요구사항

### 2.1. `conf.toml` 기반 동적 에이전트 프로파일링
- 시스템 시작 시 `conf.toml`을 읽어 에이전트 풀(Agent Pool)을 동적으로 등록합니다.
- 각 에이전트별 필수/선택 속성:
  - `name`, `role`, `model` (e.g., `gpt-4o`, `claude-3-5-sonnet`, `gemini-1.5-pro`, `ollama/...`)
  - `api_key` / `api_base` (환경 변수 치환 지원: `${OPENAI_API_KEY}`)
  - `temperature`, `max_tokens`, `max_context_window`
  - `system_prompt` (기본 페르소나 및 목표)
  - `allowed_mcp_servers` (사용 가능한 MCP 서버 식별자 리스트)
- 필수 에이전트: `Orchestrator` (토론 중재, 발언권 부여, 합의 판정, 최종 산출물 병합)

### 2.2. 백엔드 MCP Host & Tool Integration
- 백엔드에 MCP Client 관리자를 내장하여 외부/로컬 MCP 서버(Filesystem, Web Search, Terminal, GitHub 등)와 연결합니다.
- 각 에이전트는 자신에게 할당된 MCP 도구를 Function Calling 형태로 호출하고, 실행 결과를 관측(Observation)하여 토론에 반영합니다.

### 2.3. 멀티 에이전트 토론 & 의사결정 워크플로우 (Orchestration Engine)
1. **에이전트 선택 & 스폰**:
   - 유저는 UI에서 토론에 참여시킬 에이전트(Orchestrator 필수 + Specialist B, C, D...)를 선택하고, 세션 전용 커스텀 지침(Custom Instruction)을 추가 주입합니다.
2. **라운드 기반 토론 & 도구 사용**:
   - 유저의 프롬프트가 입력되면 Orchestrator가 목표를 분석하고 발언 순서(Speaker Selection) 또는 서브 태스크를 분배합니다.
   - 에이전트들은 상호 피드백을 주고받으며 MCP 도구를 실행해 정보를 검증하거나 스케치 코드를 작성합니다.
3. **합의 & 최종 결과물 산출 (Consensus & Synthesis)**:
   - 각 에이전트가 완수 플래그를 전달하고 Orchestrator가 목표 충족을 검증하면 최종 산출물(답변, 아키텍처 다이어그램, 실행 가능한 코드, 프로젝트 스켈레톤 등)을 합성(Synthesize)합니다.
4. **유저 피드백 루프**:
   - 유저는 산출물을 검토한 뒤 추가 피드백을 제시하고, 에이전트들은 기존 컨텍스트를 유지한 채 반복 개선합니다.

### 2.4. 반응형 웹 GUI 요구사항
- **좌측 사이드바 (Session Sidebar)**:
  - 과거 협업/토론 세션 목록 표시 (생성일시, 세션 제목, 참여 에이전트 뱃지).
  - 신규 세션 생성 (`+ New Chat`), 세션 이름 변경 및 명시적 삭제 (`Delete`) 기능.
- **상단/설정 패널 (Agent Roster & Control)**:
  - 현재 세션에 활성화된 에이전트 카드/토글 스위치.
  - 실시간 토론 라운드 수 제한 (`Max Rounds`), 토론 전략 선택 (자유 토론 / 순차 검증 / 디베이트).
- **메인 채팅 및 토론 피드 (Interactive Debate Timeline)**:
  - 유저 메시지, 오케스트레이터 지침, 일반 에이전트 발언을 색상/아바타별로 명확히 구분.
  - MCP 도구 실행 과정(Tool Call & Output)을 접이식(Accordion/Folding) 형태로 렌더링.
  - 실시간 스트리밍 텍스트 출력 지원 (SSE/WebSocket).
- **우측/하단 산출물 뷰어 (Artifact Viewer)**:
  - 최종 코드, 마크다운 문서, Mermaid 다이어그램을 탭 형태로 렌더링하고 원클릭 복사/다운로드 기능 제공.

---

## 3. `conf.toml` 샘플 구조 명세

```toml
[app]
host = "0.0.0.0"
port = 8000
db_url = "sqlite:///./multiagent.db"
debug = true

# MCP 서버 연동 설정
[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"]

[mcp_servers.brave_search]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-brave-search"]
env = { BRAVE_API_KEY = "${BRAVE_API_KEY}" }

# 1. 필수 오케스트레이터 에이전트
[agents.orchestrator]
name = "Master Orchestrator"
role = "Moderator & Synthesizer"
model = "openai/gpt-4o"
api_key = "${OPENAI_API_KEY}"
temperature = 0.2
max_tokens = 4096
allowed_mcp_servers = ["filesystem", "brave_search"]
system_prompt = """
당신은 멀티 에이전트 토론을 이끄는 수석 오케스트레이터입니다.
유저의 요구사항을 분해하고 적절한 전문가 에이전트에게 발언권을 부여하며,
토론이 완료되면 결과를 통합하여 최종 아티팩트(코드, 아키텍처 등)를 작성하세요.
"""

# 2. 소프트웨어 아키텍트 에이전트
[agents.architect]
name = "System Architect"
role = "High-Level Architecture & Tech Stack"
model = "anthropic/claude-3-5-sonnet-20241022"
api_key = "${ANTHROPIC_API_KEY}"
temperature = 0.5
allowed_mcp_servers = ["brave_search"]
system_prompt = "시스템 아키텍처 설계, 모듈 분리, 기술 스택 선정 및 Mermaid 다이어그램 작성을 전담합니다."

# 3. 시니어 코더 에이전트
[agents.coder]
name = "Senior Python Engineer"
role = "Implementation & Code Refinement"
model = "openai/gpt-4o"
api_key = "${OPENAI_API_KEY}"
temperature = 0.1
allowed_mcp_servers = ["filesystem"]
system_prompt = "구체적인 소스코드 작성, 클린 코드 원칙 준수, 스켈레톤 프로젝트 생성을 전담합니다."

# 4. 크리티컬 리뷰어 에이전트
[agents.critic]
name = "Security & Quality Critic"
role = "Code Review & Edge Case Analysis"
model = "google/gemini-1.5-pro"
api_key = "${GEMINI_API_KEY}"
temperature = 0.3
allowed_mcp_servers = []
system_prompt = "작성된 설계와 코드의 보안 취약점, 성능 병목, 엣지 케이스를 비판적으로 검토합니다."


---

4. 구현 우선순위 및 단계별 작업 지시
Phase 1: Config & Model Layer
Pydantic 모델을 통한 conf.toml 파서 구현 (환경변수 동적 해석 포함).
SQLite DB 스키마 구축 (Session, Message, AgentState, Artifact).
Phase 2: MCP Host & Agent Execution Engine
비동기 MCP Client Manager 구현 (Tool Discovery 및 Call 추상화).
LiteLLM 기반 단일 에이전트 호출 및 Tool Loop 처리기 작성.
Phase 3: Multi-Agent Orchestrator Graph
Orchestrator 중심의 상태 머신 (User Input -> Orchestrator Plan -> Agent Debate Loop -> Orchestrator Final Synthesis).
Phase 4: Responsive Web UI (NiceGUI / FastAPI)
세션 사이드바, 에이전트 셀렉터, 실시간 대화창, MCP 로그 아코디언, 산출물 뷰어 구현.
Phase 5: E2E 테스트 및 샘플 시나리오 검증

### 💡 기획 및 구현 팁
- **프론트엔드/백엔드 일체화**: Python 선호 조건이 있으므로, **FastAPI + NiceGUI** 조합을 추천합니다. Python만으로 모던 반응형 SPA(Vue/Quasar 기반 렌더링)를 만들 수 있어 별도의 React 빌드 과정 없이 단일 Uvicorn 서버로 서빙할 수 있습니다.
- **에이전트 토론 수렴 보장**: 에이전트 간 토론이 무한 루프에 빠지지 않도록 `max_rounds` 파라미터와 Orchestrator의 `is_consensus_reached` 종료 조건을 반드시 상태 그래프에 두는 것이 좋습니다.
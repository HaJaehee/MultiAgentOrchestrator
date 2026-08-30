# MADO 사용 설명서

**MADO: Multi-Agent Debate & Orchestration Platform** — `conf.json` 하나로 정의한
여러 LLM 에이전트가 MCP 도구를 써 가며 토론하고, 그 결과를 실행 가능한 산출물로
합성하는 파이썬 웹 애플리케이션입니다.

이 문서 모음은 **기술 스택 · 핵심 기술 · 워크플로우** 세 축으로 정리되어 있습니다.
처음 보신다면 [시스템 개요](01-overview/README.md)부터 읽으세요.

---

## 문서 트리

```text
docs/user_manual/
├── README.md ............................ 이 문서 (전체 지도)
│
├── 01-overview/ ......................... 무엇을 하는 시스템인가
│   ├── README.md ........................ 시스템 개요
│   ├── 01-tech-stack.md ................. 기술 스택과 선택 이유
│   └── 02-architecture.md ............... 레이어 구조와 데이터 흐름
│
├── 02-getting-started/ .................. 설치하고 띄우기
│   ├── README.md ........................ 시작하기
│   ├── 01-installation.md ............... 설치와 첫 실행
│   └── 02-configuration.md .............. conf.json 설정
│
├── 03-core/ ............................. 핵심 기술 (모듈별 원리)
│   ├── README.md ........................ 핵심 기술 개관
│   ├── 01-config-layer.md ............... 설정 레이어 (JSON + Pydantic)
│   ├── 02-agent-pool.md ................. 에이전트 풀과 페르소나
│   ├── 03-llm-integration.md ............ LiteLLM 추상화와 도구 루프
│   ├── 04-mcp-host.md ................... MCP 호스트와 클라이언트
│   ├── 05-orchestration-engine.md ....... 오케스트레이션 엔진
│   ├── 06-debate-strategies.md .......... 토론 전략 3종
│   └── 07-persistence.md ................ 데이터베이스와 세션 스냅샷
│
├── 04-workflows/ ........................ 실제로 어떻게 흘러가는가
│   ├── README.md ........................ 워크플로우 개관
│   ├── 01-debate-turn.md ................ 토론 한 턴의 생애주기
│   ├── 02-session-lifecycle.md .......... 세션 생성 → 잠금 → 재개
│   ├── 03-roster-editing.md ............. 로스터 편집
│   ├── 04-artifact-and-export.md ........ 산출물 생성과 내보내기
│   └── 05-airgap-deployment.md .......... 폐쇄망 배포
│
└── 05-reference/ ........................ 찾아보기
    ├── README.md ........................ 레퍼런스 개관
    ├── 01-http-api.md ................... HTTP API
    ├── 02-project-layout.md ............. 프로젝트 구조
    └── 03-testing.md .................... 테스트
```

---

## 목적별 길잡이

| 하려는 일 | 읽을 문서 |
| :--- | :--- |
| 이 시스템이 뭔지 5분 안에 알기 | [시스템 개요](01-overview/README.md) |
| 어떤 기술을 왜 썼는지 알기 | [기술 스택](01-overview/01-tech-stack.md) |
| 내 PC에서 띄워 보기 | [설치와 첫 실행](02-getting-started/01-installation.md) |
| 사내 LLM 게이트웨이 연결하기 | [conf.json 설정](02-getting-started/02-configuration.md) |
| 에이전트 추가·삭제하기 | [로스터 편집](04-workflows/03-roster-editing.md) |
| MCP 도구 붙이기 | [MCP 호스트](03-core/04-mcp-host.md) |
| 토론이 어떤 순서로 도는지 알기 | [토론 한 턴의 생애주기](04-workflows/01-debate-turn.md) |
| 폐쇄망에 배포하기 | [폐쇄망 배포](04-workflows/05-airgap-deployment.md) |
| 코드를 고치기 전에 구조 파악하기 | [아키텍처](01-overview/02-architecture.md), [프로젝트 구조](05-reference/02-project-layout.md) |

---

## 이 문서를 HTML 로 보기

```bash
python docs/render_user_manual.py
```

`docs/user_manual_html/` 에 사이드바 트리가 붙은 정적 사이트가 생성됩니다.
`docs/user_manual_html/index.html` 을 브라우저로 열면 됩니다.

렌더러는 **표준 라이브러리만** 씁니다. 출력물도 외부 요청이 하나도 없는
자기완결적 HTML 이라, 폐쇄망으로 폴더째 옮겨도 그대로 열립니다.

| 옵션 | 설명 |
| :--- | :--- |
| `--src DIR` | 입력 폴더 (기본 `docs/user_manual`) |
| `--out DIR` | 출력 폴더 (기본 `docs/user_manual_html`) |
| `--clean` | 출력 폴더를 먼저 비웁니다 |

---

## 관련 문서

- [README.md](../../README.md) — 저장소 최상위 안내 (설치, 기능 요약)
- [wiki/](../../wiki/README.md) — 영문 기술 위키 (설계 배경과 세부 구현)
- [CLAUDE.md](../../CLAUDE.md) — 프로젝트 명세서

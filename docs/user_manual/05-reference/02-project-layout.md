# 프로젝트 구조

> 상위: [레퍼런스 개관](README.md) · 이전: [HTTP API](01-http-api.md) · 다음: [테스트](03-testing.md)

---

## 최상위

```text
MultiAgentOrchestrator/
├── app/                      애플리케이션 소스
├── tests/                    테스트 (249개)
├── wiki/                     영문 기술 위키
├── docs/
│   ├── user_manual/          이 문서 모음 (마크다운)
│   ├── user_manual_html/     렌더링 산출물 (생성물)
│   └── render_user_manual.py 렌더러
├── mcp_servers/              포크한 MCP 서버 원본
│   └── memory_scoped/        대화별 지식 그래프 (공식 서버의 포크)
│
├── conf.example.json         설정 템플릿 (저장소에 커밋)
├── conf.json                 실제 설정 (gitignore)
├── .env.example / .env       환경변수
├── requirements.txt
│
├── setup_mcp.py              개발 PC MCP 서버 준비
├── open_browser.py           서버 응답 시 브라우저 열기
├── package_offline.py        폐쇄망 전체 번들
├── package_source.py         소스 갱신 패키지
│
├── README.md                 저장소 안내
├── LICENSE.md                라이선스 (LGPL-3.0 전문 + 제3자 고지)
└── CLAUDE.md                 프로젝트 명세서
```

생성물 (gitignore): `workspace/`, `multiagent.db`, `mcp_node/`, `mcp_sandbox/`,
`dist/`, `docs/user_manual_html/`

---

## `app/` 상세

```text
app/
├── main.py                   195   FastAPI 앱, lifespan, /api/*, CLI 진입점
├── config.py               1,035   conf.json 로더·기록기, 환경변수 치환, Pydantic
├── export.py                 201   대화 → 마크다운 문서
│
├── agents/
│   ├── base.py                94   Agent 모델, 스타일 배정
│   ├── pool.py                78   AgentPool 레지스트리
│   ├── llm.py                401   LiteLLM 호출, 도구 루프, 컨텍스트 관리
│   └── personas.py           382   세션별 인격, 구성 스냅샷
│
├── mcp/
│   ├── manager.py            431   서버 수명, 도구 색인, 작업 공간 전환
│   └── client.py             561   stdio 세션, 도구 검색·실행, stderr 갈무리
│
├── orchestration/
│   ├── engine.py           1,060   계획 → 라운드 → 합성 상태 머신
│   ├── runner.py             386   백그라운드 태스크, 이벤트 팬아웃
│   ├── strategies.py         231   토론 전략 3종
│   ├── state.py               47   DebateState, DebateMessage, ArtifactItem
│   └── control.py             59   정지 요청 / 개입 메모 우편함
│
├── database/
│   ├── models.py             132   SQLAlchemy ORM (5개 테이블)
│   └── session.py             79   비동기 엔진·세션 팩토리, init_db
│
└── ui/
    ├── app.py                516   메인 페이지 조립
    ├── personas_page.py      293   /personas/{id}
    ├── theme.py              170   색·아이콘·파비콘
    ├── clipboard.py           43   클립보드 복사
    └── components/
        ├── roster.py       1,851   에이전트 카드, MCP 칩, 전역 설정 편집
        ├── chat_feed.py      530   토론 피드, 발언 카드, 도구 아코디언
        ├── sidebar.py        344   세션 목록, 생성/이름변경/삭제
        └── artifact_viewer.py 180  산출물 탭, 복사/다운로드
```

총 9,373줄.

---

## 어디를 고쳐야 하나

| 하고 싶은 것 | 파일 |
| :--- | :--- |
| 설정 항목 추가 | `config.py` (Pydantic 모델 + 기록기) |
| 새 토론 전략 | `orchestration/strategies.py` |
| 토론 흐름 변경 | `orchestration/engine.py` |
| LLM 호출 파라미터 | `agents/llm.py` (`build_completion_kwargs`) |
| MCP 서버 다루는 방식 | `mcp/manager.py` |
| 발언 카드 모양 | `ui/components/chat_feed.py` |
| 로스터 컨트롤 | `ui/components/roster.py` |
| 산출물 렌더링 | `ui/components/artifact_viewer.py` |
| 내보내기 형식 | `export.py` |
| API 엔드포인트 | `main.py` |
| DB 스키마 | `database/models.py` |

---

## 큰 파일 두 개

**`roster.py` (1,851줄)** — 로스터 패널 하나가 이 시스템에서 가장 많은 것을
합니다. 대화 설정(참여 토글, 전략, 라운드, 작업 공간)과 전역 설정(에이전트
추가·삭제·순서·진영·도구, MCP 서버)을 한 화면에서 다루고, 그 둘의 잠금 규칙이
서로 다릅니다.

**`engine.py` (1,060줄)** — 토론 한 턴의 전 과정. 계획, 발언자 선정, 라운드
루프, 도구 실행 기록, 합성, 아티팩트 추출.

---

## 의존 방향

```text
ui/  ──▶  orchestration/  ──▶  agents/  ──▶  config.py
                │                  │
                └──────────────▶  mcp/  ──▶  config.py
                │
                └──────────────▶  database/
```

**역방향 참조가 없습니다.** 특히 `orchestration/` 은 `ui/` 를 import 하지
않습니다 — 토론 태스크가 NiceGUI 엘리먼트를 건드리지 않아야 브라우저가 사라져도
살아남습니다.

---

## 관련 문서

- [아키텍처](../01-overview/02-architecture.md) — 레이어와 데이터 흐름
- [핵심 기술 개관](../03-core/README.md) — 모듈별 내부 원리
- [테스트](03-testing.md) — 무엇이 검증되고 있는가

---

> 다음: [테스트](03-testing.md)

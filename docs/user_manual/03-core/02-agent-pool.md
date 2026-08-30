# 에이전트 풀과 페르소나

> 상위: [핵심 기술 개관](README.md) · 이전: [설정 레이어](01-config-layer.md) · 다음: [LLM 통합](03-llm-integration.md)
>
> 파일: `app/agents/base.py` · `pool.py` · `personas.py`

---

## Agent 모델

`AgentConfig`(설정)에 UI 표현을 더한 것이 `Agent`(런타임)입니다.

```python
Agent.from_config(key, cfg)   # AgentConfig + 스타일 → Agent
```

| 분류 | 필드 |
| :--- | :--- |
| 정체성 | `key`, `name`, `role`, `system_prompt` |
| LLM | `model`, `api_key`, `api_base`, `api_version`, `provider` |
| 샘플링 | `temperature`, `top_p`, `max_tokens`, `max_context_window` |
| 네트워크 | `timeout`, `num_retries`, `drop_params`, `extra_headers`, `extra_body` |
| 도구 | `allowed_mcp_servers`, `max_tool_iterations` |
| 토론 | `debate_priority`, `debate_stance` |
| 사고 | `sequential_thinking` |
| UI | `avatar`, `color`, `badge_color` |

### 두 개의 파생 속성

```python
@property
def is_live(self) -> bool:
    if self.api_base:                       # 엔드포인트가 있으면
        return True
    if self.api_key and self.api_key.strip():   # 키가 있으면
        return True
    return self.model.split("/", 1)[0] in {"ollama", "ollama_chat", "lm_studio"}
```

`False` 면 발언 차례에 `LLMUnavailableError` 가 올라옵니다. **대체 응답은
없습니다.** `endpoint_label` 은 화면에 뜨는 요약이고, 미설정 시
`"no endpoint configured"` 를 돌려줍니다.

### 색이 키에서 결정되는 이유

기본 4종(orchestrator / architect / coder / critic)은 고정 스타일 표가 있지만,
화면에서 추가한 에이전트는 그 표에 없습니다. 전부 같은 회색 로봇으로 나오면
피드에서 누가 말하는지 구분할 수 없으므로 팔레트에서 하나를 고릅니다.

```python
CUSTOM_STYLE_PALETTE[zlib.crc32(key.encode("utf-8")) % len(CUSTOM_STYLE_PALETTE)]
```

**`crc32` 를 쓴 이유**: 파이썬의 문자열 `hash()` 는 실행마다 값이 달라집니다
(해시 무작위화). 키가 같으면 언제 어느 프로세스에서 보든 같은 색이어야 합니다.

---

## AgentPool

프로세스 전체가 공유하는 싱글턴 레지스트리입니다.

```python
pool = get_agent_pool()
pool.get("architect")            # 하나
pool.get_orchestrator()          # 없으면 RuntimeError
pool.list_all()                  # 등록된 전부
pool.get_active(["coder"])       # 오케스트레이터를 항상 맨 앞에 끼워 돌려줌
```

`enabled: false` 인 에이전트는 등록 자체가 되지 않습니다.

### 제자리 갱신

```python
def reload_agent_pool() -> AgentPool:
    pool = get_agent_pool()
    pool.agent_configs = get_config().agents
    pool.reload()          # 새 객체를 만들지 않고 내용만 교체
    return pool
```

새 `AgentPool` 객체로 갈아 끼우지 않습니다. 엔진과 화면이 이 풀을 각자 참조로
붙잡고 있어서, 갈아 끼우면 이미 들고 있던 쪽은 예전 구성을 계속 봅니다.

---

## 세션별 페르소나

`conf.json` 의 에이전트 정의는 **서버 전역 기본값**입니다. 대화마다 다른 인격으로
토론시키려고 서버를 재기동할 필요는 없습니다.

```text
세션 생성 ─── 첫 메시지 전 ────┬──── 첫 메시지 ────── 그 뒤 ───▶
             🟢 편집 가능       │      🔒 잠김
             (초안 저장)        │      (스냅샷 고정)
                               │
                    이 순간 모든 에이전트의
                    AgentConfig 전체가 DB 로 굳음
```

### 편집 가능한 것과 아닌 것

| 편집 가능 (세션 오버라이드) | 편집 불가 (배포 설정) |
| :--- | :--- |
| `name` | `model`, `api_base`, `api_key` |
| `role` | `allowed_mcp_servers` |
| `system_prompt` | `temperature`, `max_tokens` … |

운영 설정은 `conf.json` 이 정본입니다. 대화의 성격을 바꾸는 것은 인격이지
엔드포인트가 아니기 때문입니다.

### 3단계 생애주기

**1단계 — 초안 (첫 메시지 전)**

페르소나 페이지(`/personas/{session_id}`)에서 저장한 값이 `session_agents` 에
초안으로 들어갑니다. 저장하지 않은 에이전트는 `conf.json` 기본값을 씁니다.

**2단계 — 잠금 (첫 메시지)**

`prepare_agents_for_turn()` 이 **모든** 활성 에이전트에 대해 그 시점의 유효값을
`session_agents` 에 기록하고 `sessions.personas_locked = True` 로 표시합니다.
기록되는 것은 페르소나뿐 아니라 `config_snapshot` — 모델·엔드포인트·키·샘플링
값·도구 권한까지 담은 `AgentConfig` 전체입니다.

**3단계 — 재개**

세션을 다시 열면 저장된 값을 그대로 씁니다. 그 사이 `conf.json` 이 어떻게
바뀌었든 상관없습니다.

### 잠긴 뒤 `conf.json` 을 바꾸면

| `conf.json` 에 한 일 | 시작한 대화 | 아직 시작 안 한 대화 |
| :--- | :--- | :--- |
| 에이전트 삭제 | 영향 없음 (스냅샷으로 계속 참여) | 사라짐 |
| 에이전트 비활성화 | 영향 없음 | 참여 안 함 |
| 모델·엔드포인트 변경 | 영향 없음 | 새 값 적용 |
| 도구 권한 변경 | 영향 없음 | 새 값 적용 |
| 에이전트 추가 | 참여 안 함 | 참여 |

이 표가 [세션 스냅샷](07-persistence.md)의 존재 이유입니다.

### 탈출구 — 설정 갱신

스냅샷이 고정이면 곤란한 경우가 있습니다. 엔드포인트가 바뀌었거나 API 키가
만료된 대화입니다. 로스터의 **설정 갱신** 버튼이 스냅샷을 지금 `conf.json` 값으로
다시 굳힙니다 — **인격은 건드리지 않으므로** 기록의 화자는 그대로입니다.
`conf.json` 에 더 이상 없는 에이전트는 손대지 않습니다.

### 발언자 정렬

```text
1. 오케스트레이터
2. conf.json 순서
3. 이 대화에만 남은 에이전트 (전역에서 삭제된 것들)
```

---

## 관련 문서

- [데이터베이스와 세션 스냅샷](07-persistence.md) — `session_agents` 스키마
- [세션 생애주기](../04-workflows/02-session-lifecycle.md) — 잠금 시점의 전체 흐름
- [로스터 편집](../04-workflows/03-roster-editing.md) — 화면에서 풀 자체를 고치기
- [토론 전략](06-debate-strategies.md) — `debate_priority` / `debate_stance` 사용처

---

> 다음: [LLM 통합](03-llm-integration.md)

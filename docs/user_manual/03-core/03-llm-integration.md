# LLM 통합

> 상위: [핵심 기술 개관](README.md) · 이전: [에이전트 풀과 페르소나](02-agent-pool.md) · 다음: [MCP 호스트](04-mcp-host.md)
>
> 파일: `app/agents/llm.py` (401줄)

`LLMCaller` 는 에이전트 하나의 발언 차례를 처리합니다. LiteLLM 을 감싸고,
시스템 프롬프트를 조립하고, 도구 호출 루프를 돌리고, 실패를 정직하게 올립니다.

---

## 한 번의 발언

```text
call_agent(agent, messages, custom_instructions, session_id)
   │
   ├─ 1. 시스템 프롬프트 조립
   │      페르소나 + 단계적 사고 프로토콜 + 세션 커스텀 지침
   │
   ├─ 2. fit_context_window()      한도를 넘으면 가운데부터 덜어냄
   ├─ 3. merge_consecutive_roles() 같은 role 이 연속되면 합침
   │
   ├─ 4. 도구 목록 조회             allowed_mcp_servers → OpenAI tool 스펙
   │
   ├─ 5. is_live 확인 ─────────────▶ False 면 LLMUnavailableError
   │
   ├─ 6. _run_litellm_loop()       도구 루프 (최대 max_tool_iterations)
   │
   └─ 7. _apply_show_steps()       show_steps=false 면 사고 과정을 잘라냄
          │
          ▼
     (응답 텍스트, 도구 호출 기록)
```

---

## 시스템 프롬프트 조립

```python
def build_system_prompt(self, agent, custom_instructions=""):
    parts = [agent.system_prompt]

    st = agent.sequential_thinking
    if st.enabled and st.mode in ("prompt", "mcp"):
        parts.append(st.render_prompt())          # {max_steps} 치환
        if st.mode == "mcp":
            parts.append(f"각 사고 단계는 반드시 '{st.mcp_server}' MCP 서버의 "
                         "sequentialthinking 도구를 호출해 기록한 뒤 진행하세요.")

    if custom_instructions:
        parts.append(f"[Session Custom Instructions]:\n{custom_instructions}")

    return "\n\n".join(p for p in parts if p)
```

세 층이 쌓입니다: **에이전트 페르소나** → **사고 프로토콜** → **이 대화의 지침**.
여기에 엔진이 발언 차례마다 전략 지침(`turn_instruction`)을 덧붙입니다.

---

## 컨텍스트 창 관리

라운드가 쌓이면 전사가 그대로 길어져 한도를 넘고, 엔드포인트는
`maximum context length ... however you requested ...` 로 400 을 돌려줍니다.

```python
budget = agent.max_context_window - agent.max_tokens - 512   # 응답분 + 여유

head, tail = messages[:2], messages[-1:]   # system+목표 / 이번 차례 지시
middle = messages[2:-1]
while middle and estimate_tokens(...) > budget:
    middle.pop(0)                          # 오래된 것부터
```

**맨 앞(목표)과 맨 뒤(이번 차례 지시)는 절대 덜어내지 않습니다.** 그 사이를
오래된 것부터 버리고, 무엇이 빠졌는지 모델에게 알립니다.

```text
[앞선 발언 7건은 컨텍스트 한도로 생략되었습니다.
 남은 기록만으로 판단하고, 생략된 내용을 지어내지 마세요.]
```

이 안내가 없으면 모델이 빈 곳을 상상으로 메웁니다.

토큰 계산은 `litellm.token_counter()` 를 먼저 쓰고, 모델을 몰라 실패하면
글자 수로 어림잡습니다 (한글은 토크나이저에 따라 글자당 1~1.5 토큰이라 넉넉히).
**토큰 계산 실패가 호출 자체를 막아서는 안 됩니다.**

### 순서가 중요합니다

```python
formatted = fit_context_window(agent, formatted)      # 1. 자르고
formatted = merge_consecutive_roles(formatted)        # 2. 합친다
```

생략 안내가 `user` role 로 들어가므로, 합치기를 나중에 해야 role 교대가
보장됩니다. Anthropic 계열은 `user`/`assistant` 가 번갈아 오지 않으면 거부합니다.

---

## 도구 루프

```text
┌─────────────────────────────────────────────┐
│ iteration 1..max_tool_iterations (기본 30)   │
│                                             │
│   litellm.acompletion(messages, tools)      │
│              │                              │
│              ├─ tool_calls 없음 → 종료 ─────┼──▶ 최종 텍스트
│              │                              │
│              └─ tool_calls 있음             │
│                   │                         │
│                   ├─ MCPManager.execute_tool()  (병렬)
│                   ├─ 결과를 messages 에 추가    │
│                   └─ on_tool_call 콜백 → UI    │
└─────────────────────────────────────────────┘
              │
              ▼ 한도 소진
      LLMUnavailableError
```

한도를 다 쓰면 **자리표시자 답변이 아니라 실패**를 올립니다. 30회를 돌고도
결론을 못 냈다면 그것은 보고되어야 할 상태입니다.

도구 호출에는 `session_id` 와 발언자 키가 스코프로 함께 실립니다. 이것을
빠뜨리면 서버가 대화를 구분하지 못해 **다른 대화의 지식 그래프를 읽습니다**.
→ [MCP 호스트](04-mcp-host.md#스코프)

---

## 요청 파라미터 조립

`build_completion_kwargs()` 가 `Agent` 필드를 LiteLLM 인자로 옮깁니다.

| Agent 필드 | LiteLLM 인자 | 비고 |
| :--- | :--- | :--- |
| `model` | `model` | `<provider>/<model>` |
| `api_base` | `api_base` | 없으면 프로바이더 기본 |
| `api_key` | `api_key` | 없고 로컬이면 자리표시자 주입 |
| `provider` | `custom_llm_provider` | 강제 지정 |
| `temperature`, `top_p`, `max_tokens` | 동일 | |
| `timeout`, `num_retries` | 동일 | |
| `drop_params` | `drop_params` | 미지원 파라미터 무시 |
| `extra_headers`, `extra_body` | 동일 | 게이트웨이 인증·라우팅 |
| `sequential_thinking` (native) | `reasoning_effort` / `thinking` | 모드에 따라 |

**키 없는 로컬 엔드포인트**: vLLM 이나 LM Studio 처럼 키가 필요 없는 서버라도
LiteLLM 은 비어 있지 않은 키 인자를 요구합니다. 그래서 자리표시자
(`sk-no-key-required`)를 자동으로 넣습니다.

---

## 단계적 사고 (Sequential Thinking)

| mode | 구현 | 대상 |
| :--- | :--- | :--- |
| `prompt` | 프로토콜 문구를 시스템 프롬프트에 주입 | 모든 모델 |
| `native` | `reasoning_effort` / `thinking` 파라미터 전달 | 추론 지원 모델 |
| `mcp` | sequential-thinking MCP 도구 사용을 강제 | 해당 서버 필요 |

`show_steps: false` 면 응답에서 결론 표지(`## 최종 결론` 등)를 찾아 그 앞을
잘라냅니다. 사고 과정은 모델이 하되 피드에는 결론만 남습니다.

```python
def _apply_show_steps(self, agent, content):
    st = agent.sequential_thinking
    if not content or not st.enabled or st.show_steps:
        return content
    for marker in CONCLUSION_MARKERS:
        idx = content.find(marker)
        if idx != -1:
            return content[idx:].strip()
    return content
```

표지를 못 찾으면 원문을 그대로 둡니다 — 잘못 자르느니 다 보여줍니다.

---

## 실패 처리

`LLMUnavailableError` 하나로 모읍니다.

```python
if not agent.is_live:
    raise LLMUnavailableError(agent,
        "api_base 도 api_key 도 설정되어 있지 않습니다. "
        f"conf.json 의 llm 또는 agents.{agent.key} 에 엔드포인트를 지정하세요.")
```

발생 조건:

- `is_live` 가 False (엔드포인트도 키도 없음)
- LiteLLM 호출이 예외로 끝남 (타임아웃, 인증, 네트워크)
- 도구 루프 한도 소진

엔진은 이것을 잡아 **연결 끊김 알림을 발언 자리에 기록**하고, 그 에이전트 키를
`state.failed_agent_keys` 에 넣습니다. 이 목록이 두 곳에 쓰입니다.

1. 최종 합성 프롬프트 — "이 에이전트는 발언하지 못했다" 를 알고 씀
2. `is_consensus_reached` 판정 — 실패자가 있으면 합의로 치지 않음

로그에는 진단에 필요한 것이 전부 남습니다.

```text
LLM call failed for System Architect
(model=anthropic/claude-3-5-sonnet-20241022, api_base=provider default): APIConnectionError: ...
```

---

## 관련 문서

- [MCP 호스트](04-mcp-host.md) — 도구가 실제로 실행되는 곳
- [오케스트레이션 엔진](05-orchestration-engine.md) — `call_agent` 를 부르는 쪽
- [conf.json 설정](../02-getting-started/02-configuration.md#llm--전역-llm-기본값) — 파라미터 레퍼런스

---

> 다음: [MCP 호스트](04-mcp-host.md)

# 토론 전략

> 상위: [핵심 기술 개관](README.md) · 이전: [오케스트레이션 엔진](05-orchestration-engine.md) · 다음: [데이터베이스와 세션 스냅샷](07-persistence.md)
>
> 파일: `app/orchestration/strategies.py` (231줄)

전략은 두 가지를 정합니다: **누가 어떤 순서로 발언하는가**, 그리고 **각 발언
차례에 어떤 지침이 붙는가.** 두 번째가 전략들의 실제 차이입니다.

---

## 순서와 진영은 에이전트가 들고 다닌다

예전에는 전략 코드에 표가 있었습니다.

```python
{"architect": 0, "coder": 1, "critic": 2}   # 과거
```

화면에서 에이전트를 만들 수 있게 되면서 이 방식은 무너졌습니다. 표에 없는 키는
전부 우선순위 10으로 묶여 **언제나 맨 뒤로** 밀렸고, 디베이트에서는 제안자도
비판자도 아닌 `others` 로 빠졌습니다.

지금은 에이전트가 자기 자리를 들고 다닙니다.

| 필드 | 뜻 | 바꾸는 방법 |
| :--- | :--- | :--- |
| `debate_priority` | 라운드 안의 순서. 낮을수록 먼저. 같으면 `conf.json` 순서 | 로스터에서 **카드 드래그** |
| `debate_stance` | `proponent` / `critic` / `neutral` | 카드 ⋮ 메뉴 |

```python
def order_by_priority(agents):
    return sorted(agents, key=lambda a: a.debate_priority)
```

파이썬 `sorted` 는 안정 정렬이라, 아무도 순서를 지정하지 않은 설정에서는
(전원이 기본값 100이므로) **파일에 적힌 순서가 그대로** 나옵니다.

코드에 에이전트 키가 나타나는 곳은 이제 한 줄뿐입니다.

```python
ORCHESTRATOR_KEY = "orchestrator"   # 라운드 밖에 섬 (계획과 합성이 그 자리)
```

이것은 역할이 아니라 **시스템의 구조**입니다.

---

## 세 가지 전략

### 순차 토론 (`sequential_debate`) — 기본값

우선순위 순으로 전원이 한 번씩. 각자는 **직전 발언을 입력으로** 받아 그 위에
쌓거나 그것을 검증합니다.

```text
라운드 1:  아키텍트 ──▶ 엔지니어 ──▶ 리뷰어
             (설계)      (구현)      (검증)
                │           │           │
                └───────────┴───────────┘
              앞사람의 결론을 이어받음
```

지침:

| 위치 | 붙는 지침 |
| :--- | :--- |
| 첫 순서 | "뒤 순서가 이어받아 검증할 수 있도록, 결론과 그 근거·가정을 명확히 구분해 제시하세요." |
| 중간 | "바로 앞 순서인 {이름}({역할})의 발언을 **입력으로** 받아 이어가세요. 새 주제를 여는 대신, 그 결론이 옳은지 당신의 전문 영역에서 검증하고 필요한 부분을 보강하거나 반증하세요." |
| 마지막 | 위 + "당신이 이번 라운드의 마지막 순서이므로, 라운드의 결론을 정리해 남기세요." |

**이 인수인계 지침이 없으면 라운드가 그냥 독백 세 개가 됩니다** — 같은 맥락을
보고 각자 따로 말하고, 아무도 앞사람의 결론을 책임지지 않습니다.

### 디베이트 (`adversarial_debate`)

제안자와 비판자가 번갈아. 진영 안에서는 우선순위 순.

```text
proponents: [아키텍트, 엔지니어]     critics: [리뷰어]
                    │
                    ▼
      아키텍트 ─▶ 리뷰어 ─▶ 엔지니어 ─▶ (neutral 들)
       제안       비판       제안
```

지침:

| 진영 | 붙는 지침 |
| :--- | :--- |
| `proponent` | "구체적인 안을 내고, 앞서 제기된 비판이 있다면 **회피하지 말고 정면으로 답하세요**." |
| `critic` | "직전 제안의 가정·빈틈·실패 조건을 구체적으로 짚고, **막연한 우려 대신 재현 가능한 반례나 근거**를 제시하세요." |
| `neutral` | 없음 |

`neutral` 은 대립이 한 바퀴 돈 뒤에 발언합니다.

**한쪽 진영이 비어 있으면** 대립 구도가 성립하지 않으므로 그냥 우선순위 순
한 줄로 돌려줍니다. 진영을 지정하지 않은 설정에서 아무도 발언하지 못하는 일이
없어야 합니다.

### 오케스트레이터 지명 (`orchestrator_led`)

매 라운드 오케스트레이터가 발언자와 순서를 정합니다. **한 라운드에 한 명만
부를 수도 있습니다** — 전원이 매 라운드 말하는 다른 전략과 다릅니다.

```python
class OrchestratorLedStrategy(BaseDebateStrategy):
    orchestrator_selects_speakers = True   # 엔진이 LLM 에게 물어봄
```

실제 선택은 **엔진**이 합니다. LLM 을 부르는 일이라 순수 함수인 전략 객체가 할
수 없습니다. 실패하면 여기 있는 우선순위 순서로 물러섭니다 — 엔드포인트가 없거나
응답이 이상해도 토론은 돌아야 합니다.
→ [오케스트레이션 엔진](05-orchestration-engine.md#발언자-지명-오케스트레이터-지명-전략)

---

## 어느 것을 쓸까

| 상황 | 추천 |
| :--- | :--- |
| 설계 → 구현 → 검증처럼 단계가 이어짐 | **순차 토론** |
| 두 안 중 무엇이 나은지, 위험을 파고들어야 함 | **디베이트** |
| 라운드마다 필요한 전문가가 다름 | **오케스트레이터 지명** |
| 라운드를 아끼고 싶음 (호출 비용) | **오케스트레이터 지명** (전원을 부르지 않음) |

전략은 로스터 패널의 선택 상자에서 대화마다 지정하고, `sessions.strategy` 에
저장됩니다.

---

## 사라진 전략

한때 **자유 토론**과 **순차 검증**이 따로 있었습니다. 순서가 키 하드코딩으로
정해지던 시절에는 둘의 발언 순서가 달랐지만, 순서가 `debate_priority` 하나로
정리되면서 두 전략은 **같은 순서로 같은 사람들을 부르게** 되었습니다. 이름과
달리 '자유 토론' 도 결국 정해진 순서대로 도는 것이었습니다.

그래서 하나로 합치고, 하는 일 그대로 '순차 토론' 이라 부릅니다.

```python
LEGACY_STRATEGY_ALIASES = {
    "free_debate": "sequential_debate",
    "sequential_review": "sequential_debate",
}
```

`sessions.strategy` 는 문자열이라, 이 표가 없으면 옛 대화가 전략을 잃고
기본값으로 떨어집니다 (그리고 화면의 선택 상자는 아는 값이 아니어서 빈칸이 됩니다).

---

## 새 전략 만들기

```python
class MyStrategy(BaseDebateStrategy):
    name = "my_strategy"
    display_name = "내 전략"

    def get_speakers_for_round(self, active_agents, round_num, state):
        specialists = order_by_priority(specialists_of(active_agents))
        return specialists

    def turn_instruction(self, agent, speakers, index, state):
        return "[내 전략] ..."

STRATEGY_MAP["my_strategy"] = MyStrategy()
```

규칙:

- `specialists_of()` 로 오케스트레이터를 빼세요
- 빈 목록을 돌려주지 마세요 (아무도 발언하지 못합니다)
- 에이전트 키를 하드코딩하지 마세요 — `debate_priority` / `debate_stance` 를 읽으세요

---

## 관련 문서

- [오케스트레이션 엔진](05-orchestration-engine.md) — 전략을 부르는 쪽
- [로스터 편집](../04-workflows/03-roster-editing.md) — 순서와 진영을 화면에서 바꾸기
- [conf.json 설정](../02-getting-started/02-configuration.md#agents--에이전트-정의) — 필드 레퍼런스

---

> 다음: [데이터베이스와 세션 스냅샷](07-persistence.md)

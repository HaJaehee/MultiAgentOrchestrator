"""conf.toml 에 에이전트를 추가했을 때 기존 대화의 로스터가 어떻게 보이는가.

`sessions.active_agents` 는 켜 둔 것만 담는 허용 목록입니다. 목록에 없다는 사실
하나로는 "사용자가 끈 에이전트" 와 "그 대화를 설정할 때는 없던 에이전트" 를
구분할 수 없고, 구분하지 않으면 둘 중 하나가 반드시 틀립니다.

* 전부 꺼진 것으로 보면 → 새로 추가한 에이전트가 모든 기존 대화에서 흐리게 나온다.
* 전부 켜진 것으로 보면 → 사용자가 끈 에이전트가 새로고침할 때마다 되살아난다.

그래서 그 대화를 저장할 때 존재하던 에이전트(`known_agents`)를 함께 적고,
여기서 세 경우를 갈라 놓습니다.
"""

import pytest

from app.ui.components.roster import AgentRosterControl

FOUR = ["orchestrator", "architect", "coder", "critic"]


def selected(key, active, known):
    return AgentRosterControl._is_selected(key, active, known)


def test_agent_kept_on_stays_on():
    assert selected("architect", FOUR, FOUR) is True


def test_agent_the_user_turned_off_stays_off():
    """끈 상태가 새로고침을 넘겨 살아남아야 합니다. 엔진도 같은 목록을 읽습니다."""
    active = ["orchestrator", "coder", "critic"]      # architect 를 껐다
    assert selected("architect", active, FOUR) is False


def test_agent_added_to_conf_after_the_session_shows_up_enabled():
    """이번에 고친 증상. 새 에이전트는 기존 대화에서도 켜진 채로 보입니다."""
    assert selected("researcher", FOUR, FOUR) is True


def test_new_agent_and_disabled_agent_are_told_apart():
    active = ["orchestrator", "coder", "critic"]      # architect 를 껐다
    known = FOUR                                      # 그때는 넷뿐이었다
    assert selected("architect", active, known) is False, "끈 것은 꺼진 채로"
    assert selected("researcher", active, known) is True, "새로 생긴 것은 켜진 채로"


@pytest.mark.parametrize("key", FOUR + ["researcher"])
def test_sessions_older_than_the_record_light_everything_up(key):
    """`known_agents` 가 생기기 전 대화는 그때 무엇이 있었는지 알 수 없습니다.

    새 에이전트가 옛 대화 전부에서 꺼져 보이는 것보다, 한 번 켜진 채로 보이고
    그 대화를 열어 보는 순간 기록이 남아 정확해지는 편이 낫습니다.
    """
    assert selected(key, ["orchestrator", "coder"], []) is True


def test_a_session_with_no_roster_at_all_enables_everything():
    assert selected("architect", [], []) is True
    assert selected("architect", [], FOUR) is True

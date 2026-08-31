"""발언 카드를 세 줄로 접을지 판단하는 규칙.

발언이 끝나면 본문을 세 줄만 남기고 접습니다. 그런데 짧은 발언까지 접으면 아무 일도
하지 않는 펼치기 버튼만 붙습니다 — 한 줄짜리 "이견 없습니다." 옆의 화살표는 누를
이유가 없는데 자리를 차지합니다.

실제 줄 수는 그려 봐야 알 수 있고, 그걸 재려면 카드마다 브라우저에 물어봐야 합니다.
대신 글자 수와 줄바꿈으로 어림합니다. 틀리는 방향은 하나뿐입니다 — 세 줄에 가까운
글에 버튼이 하나 더 붙는 것이고, 그건 눈에 거슬리는 정도입니다.
"""

import pytest

from app.ui.components.chat_feed import (
    CLAMP_MIN_CHARS,
    CLAMP_MIN_NEWLINES,
    ChatFeed,
    is_clampable,
)


@pytest.mark.parametrize("content", [
    "",
    "이견 없습니다.",
    "확인했습니다. 제안하신 방향에 동의하며 추가로 덧붙일 내용은 없습니다.",
    "첫 줄\n둘째 줄",
])
def test_short_speeches_get_no_expand_button(content):
    assert is_clampable(content) is False


@pytest.mark.parametrize("content", [
    "가" * (CLAMP_MIN_CHARS + 1),
    "\n".join(f"{i}번 항목" for i in range(CLAMP_MIN_NEWLINES + 1)),
    "### 제목\n\n본문 한 줄\n\n- 항목",
])
def test_long_speeches_are_collapsible(content):
    assert is_clampable(content) is True


def test_the_threshold_is_about_three_lines():
    """카드 폭에서 한 줄이 대략 60~70자이므로 세 줄이면 200자 안팎입니다."""
    assert is_clampable("가" * CLAMP_MIN_CHARS) is False
    assert is_clampable("가" * (CLAMP_MIN_CHARS + 1)) is True


def test_a_code_block_counts_by_its_line_breaks():
    """긴 글자 수가 아니라 줄바꿈으로도 걸립니다. 코드는 짧아도 줄이 많습니다."""
    snippet = "```python\nx = 1\ny = 2\n```"
    assert len(snippet) < CLAMP_MIN_CHARS
    assert is_clampable(snippet) is True


def test_none_is_treated_as_empty():
    """스트리밍이 시작될 때의 카드는 본문이 없습니다."""
    assert is_clampable(None) is False


# --------------------------------------------------------------- 자동 스크롤
#
# 카드를 펼치는 이유는 그것을 읽기 위해서인데, 그동안에도 다른 에이전트의 글자는
# 계속 도착합니다. 매 청크마다 화면이 맨 아래로 끌려가면 읽던 자리를 잃고, 다시
# 올라가면 곧바로 또 끌려 내려갑니다. 그래서 펼쳐 둔 카드가 하나라도 있으면
# 따라가지 않고, 전부 접히면 다시 따라갑니다.
#
# 여기서 확인하는 것은 그 판단(`following`)과 장부(`_user_expanded`)입니다. 실제
# 스크롤은 브라우저가 하지만, 언제 그것을 부를지는 이 규칙이 정합니다.

LONG_ENOUGH = "가" * (CLAMP_MIN_CHARS + 1)


def _feed():
    async def noop(*_args):
        pass

    return ChatFeed(noop, on_interject=noop, on_stop=noop)


def _card(msg_id: str, content: str = LONG_ENOUGH, collapsed: bool = True):
    """화면 없이 카드 상태만 흉내 냅니다 (`_render_card` 가 만드는 딕셔너리)."""
    return {"id": msg_id, "content": content, "collapsed": collapsed}


def test_a_fresh_feed_follows_the_stream():
    assert _feed().following is True


def test_expanding_a_card_stops_the_feed_from_following():
    feed = _feed()
    card = _card("m1")

    feed._toggle_card(card)

    assert card["collapsed"] is False
    assert feed.following is False


def test_collapsing_it_again_resumes_following():
    feed = _feed()
    card = _card("m1")

    feed._toggle_card(card)
    feed._toggle_card(card)

    assert feed.following is True


def test_following_resumes_only_when_every_card_is_collapsed():
    """두 개를 펼쳤다면 하나를 접어도 아직 읽는 중입니다."""
    feed = _feed()
    first, second = _card("m1"), _card("m2")

    feed._toggle_card(first)
    feed._toggle_card(second)
    feed._toggle_card(first)

    assert feed.following is False
    feed._toggle_card(second)
    assert feed.following is True


def test_a_card_drawn_open_does_not_pause_the_feed():
    """생성 중인 카드는 펼쳐진 채로 그려집니다 (`_render_card(streaming=True)`).

    그것까지 세면 토론이 도는 내내 자동 스크롤이 꺼집니다. 멈추는 것은 사람이
    버튼을 누른 경우뿐이어야 하므로, 펼친 채로 그리는 경로는 장부를 건드리지
    않습니다.
    """
    feed = _feed()
    card = _card("m1")

    feed._apply_clamp(card, collapsed=False)   # 그리는 쪽이 하는 일

    assert card["collapsed"] is False
    assert feed._user_expanded == set()
    assert feed.following is True


def test_a_short_card_never_pauses_the_feed():
    """접을 것이 없는 발언에는 펼침 버튼도 없습니다. 눌러도 멈출 이유가 없습니다."""
    feed = _feed()

    feed._toggle_card(_card("m1", content="이견 없습니다."))

    assert feed.following is True


def test_switching_sessions_forgets_the_expanded_cards():
    """카드가 사라졌는데 장부가 남으면, 다음 대화가 있지도 않은 카드 때문에
    자동 스크롤이 꺼진 채로 시작합니다."""
    feed = _feed()
    feed._toggle_card(_card("m1"))
    assert feed.following is False

    feed.clear()

    assert feed.following is True

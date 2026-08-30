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

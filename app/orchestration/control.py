"""진행 중인 토론에 사람이 끼어드는 통로.

토론 태스크는 한번 뜨면 끝까지 혼자 도는 것이 원래 설계였습니다. 그래서
"지금까지만 정리하고 멈춰" 나 "그 방향 말고 이쪽으로" 를 전할 방법이 없었고,
할 수 있는 것은 태스크를 통째로 죽이는 것(`DebateRunner.cancel()`)뿐이었습니다.
그러면 진행 중이던 발언은 잘리고 최종 합성 단계는 아예 돌지 않아, 지금까지의
토론으로 산출물을 뽑을 기회가 사라집니다.

`TurnControl` 은 그 사이에 놓이는 아주 작은 우편함입니다. 화면 쪽 코루틴이
여기에 요청을 넣어 두면, 엔진이 발언과 발언 사이의 안전한 지점에서 꺼내 봅니다.

* **정지 요청** — 태스크를 죽이지 않습니다. 진행 중인 발언은 끝까지 받고,
  남은 라운드를 건너뛰어 곧장 최종 합성으로 넘어갑니다.
* **개입 메모** — 다음 발언자의 맥락에 유저 발언으로 끼어듭니다.

경계를 넘어 공유되는 상태는 이 객체 하나뿐이고, 같은 이벤트 루프 안에서만
읽고 쓰므로 락이 필요 없습니다. 엔진은 이 모듈만 알면 되고 UI 도 러너도
모릅니다.
"""

from typing import List


class TurnControl:
    """한 턴에 대한 정지 요청과 개입 메모를 담아 두는 우편함."""

    def __init__(self) -> None:
        self._stop_requested = False
        self._notes: List[str] = []

    # -------------------------------------------------- 정지

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def request_stop(self) -> None:
        """다음 안전 지점에서 토론을 접고 합성으로 넘어가도록 표시합니다."""
        self._stop_requested = True

    # -------------------------------------------------- 개입

    @property
    def pending_notes(self) -> List[str]:
        """아직 토론에 반영되지 않고 대기 중인 개입 메모."""
        return list(self._notes)

    def add_note(self, text: str) -> bool:
        """개입 메모를 대기열에 넣습니다. 빈 문자열은 무시하고 False 를 돌려줍니다."""
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        self._notes.append(cleaned)
        return True

    def drain_notes(self) -> List[str]:
        """대기 중인 메모를 전부 꺼내고 대기열을 비웁니다."""
        notes, self._notes = self._notes, []
        return notes

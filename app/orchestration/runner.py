"""토론을 브라우저 세션에서 떼어 내 백그라운드로 굴리는 실행기.

예전에는 `engine.run_turn()` 이 채팅 입력 핸들러 안에서 그대로 await 되었습니다.
그래서 페이지를 새로고침하거나 페르소나 화면에 다녀오면:

* NiceGUI 가 그 클라이언트를 지우고, 코루틴이 붙잡고 있던 슬롯의 부모 엘리먼트가
  사라집니다. 이어지는 UI 갱신은 `The parent element this slot belongs to has been
  deleted.` 로 터졌고, 그 예외가 토론 자체를 중단시켰습니다.
* 살아남더라도 진행 상황을 다시 볼 방법이 없었습니다.

여기서는 토론을 세션 단위 `asyncio.Task` 로 띄우고, 이벤트를 두 갈래로 보냅니다.

1. **정본(canonical) 스냅샷** — 지금까지 나온 발언과 진행 상태를 `TurnRun` 이 들고
   있습니다. 새로 붙는 화면은 이걸 그대로 그리면 됩니다.
2. **구독 큐** — 붙어 있는 화면마다 큐 하나. 화면이 죽으면 큐만 버리고 토론은
   계속됩니다.

UI 쪽 코드는 이 모듈을 import 하지만, 이 모듈은 UI 를 전혀 모릅니다. 엔진 태스크가
NiceGUI 엘리먼트를 건드릴 일이 없으니 클라이언트가 사라져도 터질 곳이 없습니다.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from app.orchestration.engine import OrchestratorEngine, get_orchestrator_engine

logger = logging.getLogger(__name__)

# 구독 큐 상한. 브라우저 하나가 느려도 토론을 붙잡지 않도록 넉넉히 잡되,
# 무한히 쌓이지는 않게 합니다. 넘치면 그 화면은 스냅샷으로 다시 맞춥니다.
MAX_QUEUED_EVENTS = 2000


class TurnRun:
    """진행 중이거나 방금 끝난 토론 한 턴."""

    def __init__(self, session_id: str, user_prompt: str):
        self.session_id = session_id
        self.user_prompt = user_prompt
        self.status: str = "running"  # running | completed | failed | cancelled
        self.error: Optional[str] = None
        self.busy: bool = True
        self.status_text: str = "토론 준비 중..."
        self.round_info: str = "Debating"

        # 이번 턴에 나온 발언 (스트리밍 중인 것 포함). id 로 중복 제거합니다.
        self.messages: List[Dict[str, Any]] = []
        self._message_index: Dict[str, int] = {}
        self.streaming_ids: Set[str] = set()
        self.artifacts: List[Dict[str, Any]] = []

        self.task: Optional[asyncio.Task] = None
        self._subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = set()

    # -------------------------------------------------- 구독

    def subscribe(self) -> "asyncio.Queue[Dict[str, Any]]":
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _fanout(self, event: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 화면이 따라오지 못하고 있습니다. 큐를 버리면 그 화면은 다음
                # 스냅샷에서 복구됩니다. 토론은 여기서 멈추지 않습니다.
                logger.warning("Dropping a slow debate subscriber for session %s", self.session_id)
                self._subscribers.discard(queue)

    # -------------------------------------------------- 상태 적용

    def apply(self, event: Dict[str, Any]) -> None:
        """이벤트를 정본 스냅샷에 반영합니다. 새로 붙는 화면이 이걸 그립니다."""
        etype = event.get("type")

        if etype == "status_changed":
            speaker = event.get("speaker", "")
            status = event.get("status", "")
            round_num = event.get("round", "")
            self.busy = True
            self.status_text = f"[{speaker}] 발언 및 분석 중..." if speaker else f"상태: {status}"
            self.round_info = f"Round {round_num}" if round_num else "Debating"

        elif etype == "round_started":
            r = event.get("round", 1)
            mr = event.get("max_rounds", 3)
            self.busy = True
            self.status_text = f"Round {r}/{mr} 전문가 토론 진행 중..."
            self.round_info = f"Round {r}/{mr}"

        elif etype == "message_stream_start":
            msg = dict(event.get("message", {}))
            msg_id = msg.get("id", "")
            if msg_id:
                self._upsert(msg)
                self.streaming_ids.add(msg_id)

        elif etype == "message_stream_chunk":
            msg_id = event.get("message_id", "")
            idx = self._message_index.get(msg_id)
            if idx is not None:
                self.messages[idx]["content"] += event.get("delta", "")

        elif etype == "message_added":
            msg = dict(event.get("message", {}))
            msg_id = msg.get("id", "")
            if msg_id:
                self._upsert(msg)
                self.streaming_ids.discard(msg_id)

        elif etype == "artifacts_synthesized":
            self.artifacts = list(event.get("artifacts", []))

        elif etype == "turn_completed":
            self.busy = False
            self.streaming_ids.clear()
            failed = event.get("failed_agents") or []
            if failed:
                self.status_text = f"토론 완료 — 응답하지 못한 에이전트: {', '.join(failed)}"
                self.round_info = "Incomplete"
            else:
                self.status_text = "토론 완료 및 최종 아티팩트 합성 완료"
                self.round_info = "Done"

    def _upsert(self, msg: Dict[str, Any]) -> None:
        msg_id = msg["id"]
        idx = self._message_index.get(msg_id)
        if idx is None:
            self._message_index[msg_id] = len(self.messages)
            self.messages.append(msg)
        else:
            self.messages[idx] = msg

    def snapshot(self) -> Dict[str, Any]:
        """지금 화면을 그리는 데 필요한 전부."""
        return {
            "session_id": self.session_id,
            "status": self.status,
            "error": self.error,
            "busy": self.busy,
            "status_text": self.status_text,
            "round_info": self.round_info,
            "messages": [dict(m) for m in self.messages],
            "streaming_ids": set(self.streaming_ids),
            "artifacts": [dict(a) for a in self.artifacts],
        }


class DebateRunner:
    """세션별 토론 태스크의 소유자. 프로세스 전체에서 하나만 씁니다."""

    def __init__(self, engine: Optional[OrchestratorEngine] = None):
        self._engine = engine
        self._runs: Dict[str, TurnRun] = {}

    @property
    def engine(self) -> OrchestratorEngine:
        if self._engine is None:
            self._engine = get_orchestrator_engine()
        return self._engine

    def get(self, session_id: str) -> Optional[TurnRun]:
        return self._runs.get(session_id)

    def is_running(self, session_id: str) -> bool:
        run = self._runs.get(session_id)
        return run is not None and run.status == "running"

    def start(self, session_id: str, user_prompt: str) -> TurnRun:
        """토론을 백그라운드에서 시작합니다. 이미 돌고 있으면 그 실행을 돌려줍니다."""
        existing = self._runs.get(session_id)
        if existing is not None and existing.status == "running":
            return existing

        run = TurnRun(session_id, user_prompt)
        self._runs[session_id] = run

        async def on_event(event: Dict[str, Any]) -> None:
            run.apply(event)
            run._fanout(event)  # noqa: SLF001 - 같은 모듈 안의 협력 객체입니다

        async def driver() -> None:
            try:
                await self.engine.run_turn(
                    session_id=session_id, user_prompt=user_prompt, on_event=on_event
                )
                run.status = "completed"
            except asyncio.CancelledError:
                run.status = "cancelled"
                run.error = "토론이 취소되었습니다."
                raise
            except Exception as exc:  # noqa: BLE001 - 어떤 실패든 화면에 알려야 합니다
                logger.error(f"Debate turn failed for session {session_id}: {exc}", exc_info=True)
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
            finally:
                run.busy = False
                run.streaming_ids.clear()
                if run.status == "failed":
                    run.status_text = f"오류로 중단됨: {run.error}"
                    run.round_info = "Error"
                elif run.status == "cancelled":
                    run.status_text = "토론이 취소되었습니다."
                    run.round_info = "Cancelled"
                run._fanout({  # noqa: SLF001
                    "type": "run_finished",
                    "status": run.status,
                    "error": run.error,
                })

        # asyncio.create_task 로 띄운 태스크는 NiceGUI 슬롯 스택을 물려받지 않습니다.
        # 즉 이 안에서는 UI 엘리먼트를 만들 수 없고, 만들 일도 없습니다.
        run.task = asyncio.create_task(driver(), name=f"debate-{session_id}")
        return run

    async def cancel(self, session_id: str) -> bool:
        run = self._runs.get(session_id)
        if run is None or run.task is None or run.task.done():
            return False
        run.task.cancel()
        try:
            await run.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return True

    async def shutdown(self) -> None:
        """서버 종료 시 남은 토론 태스크를 정리합니다."""
        for session_id in list(self._runs):
            await self.cancel(session_id)

    def forget(self, session_id: str) -> None:
        """세션이 삭제됐을 때 스냅샷까지 버립니다."""
        run = self._runs.pop(session_id, None)
        if run is not None and run.task is not None and not run.task.done():
            run.task.cancel()


_runner: Optional[DebateRunner] = None


def get_debate_runner() -> DebateRunner:
    global _runner
    if _runner is None:
        _runner = DebateRunner()
    return _runner

import json
import logging
import time
from typing import Any, Callable, Coroutine, Dict, Iterable, List, Optional, Set
from nicegui import ui
from app.agents.base import style_for_agent
from app.ui.clipboard import copy_to_clipboard

logger = logging.getLogger(__name__)


# DB 에서 다시 그릴 때 도구 출력의 상한(글자).
#
# 파일 읽기나 샌드박스 실행 결과는 수십 KB 가 되기도 합니다. 지난 대화를 다시
# 열면 그 세션의 모든 도구 출력이 한꺼번에 들어오는데, 아코디언이 접혀 있어도
# 내용은 DOM 에 그대로 만들어집니다. 화면에서는 앞부분만 보여주고, 전문은 세션
# 저장 파일에 남깁니다 (저장은 DB 에서 직접 읽습니다).
MAX_RELOADED_TOOL_OUTPUT = 4000


def clip_tool_output(text: str, limit: int = MAX_RELOADED_TOOL_OUTPUT) -> str:
    """긴 도구 출력을 화면용으로 자릅니다. 자른 사실과 전문의 위치를 함께 알립니다."""
    body = text or ""
    if len(body) <= limit:
        return body
    hidden = len(body) - limit
    return (
        f"{body[:limit]}\n\n"
        f"… {hidden:,}자 생략 — 전문은 세션 저장(💾) 파일에 있습니다."
    )


# 입력창 문구. 토론이 도는 중에는 같은 칸이 "새 요청" 이 아니라 "개입" 으로
# 동작하므로, 무엇이 될지 문구로 먼저 알려 줍니다. props 로 넘어가는 값이라
# 큰따옴표는 쓰지 않습니다.
IDLE_PLACEHOLDER = "멀티 에이전트에게 토론 및 설계를 요청할 목표/질문을 입력하세요..."
INTERJECT_PLACEHOLDER = "토론 진행 중 — 지금 보내면 다음 발언 차례에 개입으로 전달됩니다"


# 접힌 카드가 보여주는 줄 수. 값 자체는 theme.py 의 `.chat-body-clamped` 가
# 정하고, 여기서는 문구에만 씁니다.
CLAMP_LINES = 3

# 펼치기 버튼을 달지 정하는 어림. 카드 폭에서 text-sm 한 줄이 대략 60~70자이므로
# 세 줄이면 200자쯤 됩니다. 실제 줄 수는 그려 봐야 알 수 있지만, 그걸 재려고 매
# 카드마다 브라우저에 물어보는 것보다 이 어림이 낫습니다. 짧은 글에 버튼이 하나
# 더 붙는 것이 최악이고, 그건 눈에 거슬리는 정도입니다.
CLAMP_MIN_CHARS = 200
CLAMP_MIN_NEWLINES = 3


def is_clampable(content: str) -> bool:
    """세 줄을 넘길 만한 글인지 (펼치기 버튼을 달지 결정)."""
    body = content or ""
    return len(body) > CLAMP_MIN_CHARS or body.count("\n") >= CLAMP_MIN_NEWLINES


def _card_classes(msg_type: str, sender_key: str) -> str:
    """말풍선 배경. 실패 안내는 발언과 확실히 구분되어야 합니다."""
    if msg_type == "error":
        return "bg-rose-950/40 border-rose-700/70"
    if msg_type == "user" or sender_key == "user":
        return "bg-blue-950/40 border-blue-800/60"
    if msg_type == "orchestrator" or sender_key == "orchestrator":
        return "bg-indigo-950/50 border-indigo-500/70"
    return "bg-slate-900/90 border-slate-800"


class ChatFeed:
    """Interactive multi-agent debate timeline with styled chat bubbles and collapsible MCP tool logs."""

    def __init__(
        self,
        on_send_message: Callable[[str], Coroutine[None, None, None]],
        on_interject: Optional[Callable[[str], Coroutine[None, None, None]]] = None,
        on_stop: Optional[Callable[[], Coroutine[None, None, None]]] = None,
    ):
        self.on_send_message = on_send_message
        # 토론이 도는 중에 들어온 입력과 정지 버튼의 행선지. 주어지지 않으면
        # 예전처럼 토론 중에는 입력을 잠그고 정지 버튼도 숨깁니다.
        self.on_interject = on_interject
        self.on_stop = on_stop
        self.scroll_area: Optional[ui.scroll_area] = None
        self.message_container: Optional[ui.column] = None
        self.status_bar: Optional[ui.row] = None
        self.status_label: Optional[ui.label] = None
        self.status_spinner: Optional[ui.spinner] = None
        self.input_field: Optional[ui.input] = None
        self.send_button: Optional[ui.button] = None
        self.stop_button: Optional[ui.button] = None
        self.round_badge: Optional[ui.badge] = None
        self.is_busy: bool = False
        self._stop_pending: bool = False
        self._active_streams: Dict[str, Dict[str, Any]] = {}
        self._placeholder: Optional[ui.column] = None

        # 생성 중 표시. 초가 올라가는 것이 "멈추지 않았다" 는 가장 확실한 신호라,
        # 상태 문구가 바뀔 때마다(= 발언자나 단계가 바뀔 때마다) 다시 셉니다.
        self.progress_bar: Optional[ui.element] = None
        self.elapsed_badge: Optional[ui.badge] = None
        self.live_strip: Optional[ui.row] = None
        self.live_label: Optional[ui.label] = None
        self.live_elapsed: Optional[ui.label] = None
        self._busy_since: Optional[float] = None
        self._status_text: str = ""

        # 사용자가 직접 펼쳐 둔 카드의 message id. 하나라도 있으면 자동 스크롤을
        # 멈춥니다 — 읽는 중에 화면이 밑으로 끌려가면 읽을 수가 없습니다.
        # 스트리밍 카드는 펼쳐진 채로 그려지지만 여기 들어오지 않습니다. 그건
        # 사람이 편 것이 아니라 기본 상태이고, 그것까지 세면 토론이 도는 동안
        # 자동 스크롤이 영영 꺼집니다.
        self._user_expanded: Set[str] = set()
        self.follow_button: Optional[ui.button] = None

    # ------------------------------------------------------------------ 수명

    @property
    def alive(self) -> bool:
        """이 피드가 아직 살아 있는 페이지에 붙어 있는지.

        토론은 백그라운드에서 계속 굴러가므로, 새로고침으로 버려진 화면에
        늦게 도착한 이벤트가 들어올 수 있습니다. 그때 조용히 무시합니다.
        """
        return self.message_container is not None and not self.message_container.is_deleted

    # ------------------------------------------------------------------ 구성

    def build_ui(self) -> ui.column:
        # flex-nowrap 없이는 내용이 카드보다 커질 때 입력 바가 다음 열로 줄바꿈되어
        # 화면 밖으로 밀려납니다 (NiceGUI 컬럼 기본값이 flex-wrap: wrap).
        with ui.column().classes(
            "w-full h-full flex flex-col flex-nowrap justify-between overflow-hidden"
        ) as root:
            # 1. Status Indicator Bar
            with ui.column().classes("w-full gap-1 flex-shrink-0"):
                with ui.row().classes(
                    "w-full items-center justify-between px-3 py-1.5 bg-slate-900/90 border border-slate-800 rounded-lg text-xs"
                ) as self.status_bar:
                    with ui.row().classes("items-center gap-2 min-w-0"):
                        self.status_spinner = ui.spinner("dots", size="sm", color="indigo-4")
                        self.status_spinner.set_visibility(False)
                        self.status_label = ui.label("대기 중").classes("font-semibold text-slate-300")
                        # 경과 시간. 상태 막대는 스크롤과 무관하게 늘 보이므로,
                        # 타임라인이 아무리 길어져도 여기서 초가 올라갑니다.
                        self.elapsed_badge = ui.badge("", color="indigo-9").props("dense text-[10px]")
                        self.elapsed_badge.set_visibility(False)
                    with ui.row().classes("items-center gap-2 flex-shrink-0"):
                        self.follow_button = (
                            ui.button("맨 아래로", icon="vertical_align_bottom",
                                      on_click=self._handle_follow)
                            .props("flat dense no-caps color=amber-4 size=sm")
                            .tooltip(
                                "펼쳐 둔 카드가 있어 자동 스크롤을 멈춘 상태입니다. "
                                "카드를 다시 접으면 새 발언을 따라 내려갑니다."
                            )
                        )
                        self.follow_button.set_visibility(False)
                        self.stop_button = (
                            ui.button("정지", icon="stop_circle", on_click=self._handle_stop)
                            .props("flat dense no-caps color=rose-4 size=sm")
                            .tooltip(
                                "남은 라운드를 건너뛰고 지금까지의 토론으로 최종 산출물을 만듭니다. "
                                "진행 중인 발언은 끝까지 받습니다."
                            )
                        )
                        self.stop_button.set_visibility(False)
                        self.round_badge = ui.badge("Ready", color="slate-700").props("dense")

                # 쓸려 가는 막대. 글자가 한동안 오지 않아도 무언가 돌고 있다는
                # 것을 한눈에 보여줍니다.
                self.progress_bar = ui.element("div").classes("feed-progress w-full")
                self.progress_bar.set_visibility(False)

            # 2. Scrollable Messages Timeline (Fills all remaining vertical space)
            with ui.scroll_area().classes("w-full flex-grow my-2 pr-2 min-h-0") as self.scroll_area:
                self.message_container = ui.column().classes("w-full gap-3 debate-timeline")
                with self.message_container:
                    self._render_empty_placeholder()

            # 2-b. 생성 중 줄. 타임라인 **밖**, 대화가 흘러나오는 바로 그 자리에
            #      둡니다. 안에 두면 카드 하나를 펼쳐 자동 스크롤이 멈춘 순간
            #      화면 밖으로 밀려나, 정작 필요한 때 보이지 않습니다.
            with ui.row().classes(
                "w-full items-center gap-2 px-3 py-1.5 mb-2 rounded-lg live-strip flex-shrink-0"
            ) as self.live_strip:
                ui.html('<span class="live-dots"><i></i><i></i><i></i></span>')
                self.live_label = ui.label("").classes(
                    "text-xs font-semibold text-indigo-200 truncate min-w-0"
                )
                self.live_elapsed = ui.label("").classes(
                    "text-xs font-mono text-indigo-300 flex-shrink-0 ml-auto"
                )
            self.live_strip.set_visibility(False)

            # 경과 시간은 이벤트가 아니라 시계가 갱신합니다. 도구가 30초씩 걸리는
            # 동안에는 서버에서 아무 이벤트도 오지 않기 때문입니다.
            ui.timer(1.0, self._tick_elapsed)

            # 3. Input Bar
            with ui.row().classes("w-full items-center gap-2 p-2 bg-slate-900 border border-slate-800 rounded-xl shadow-lg flex-shrink-0"):
                self.input_field = ui.input(
                    placeholder=IDLE_PLACEHOLDER,
                ).props("outlined dark dense autogrow").classes("flex-grow text-sm").on("keydown.enter", self._handle_enter)

                self.send_button = ui.button(
                    icon="send",
                    on_click=self._handle_send,
                ).props("unelevated color=indigo-6 round")

        return root

    def _render_empty_placeholder(self) -> None:
        with ui.column().classes("w-full items-center justify-center py-16 text-center text-slate-500") as self._placeholder:
            ui.icon("forum", size="xl").classes("text-slate-700 mb-2")
            ui.label("새로운 토론을 시작해 보세요").classes("text-base font-bold text-slate-400")
            ui.label("유저 요청을 입력하면 Master Orchestrator가 목표를 분해하고 전문가 토론을 진행합니다.").classes("text-xs max-w-md")

    # ------------------------------------------------------------------ 입력

    async def _handle_send(self) -> None:
        """같은 입력칸이 상황에 따라 새 턴이 되기도, 개입이 되기도 합니다.

        토론이 도는 중에 보낸 글로 새 턴을 시작할 수는 없습니다 (러너가 세션당
        하나만 돌립니다). 그렇다고 입력을 잠가 버리면 사람이 방향을 고칠 방법이
        토론이 끝날 때까지 없으므로, 진행 중이면 개입으로 보냅니다.
        """
        if not self.input_field:
            return
        text = (self.input_field.value or "").strip()
        if not text:
            return

        if self.is_busy:
            if self.on_interject is None:
                return
            self.input_field.value = ""
            await self.on_interject(text)
            return

        self.input_field.value = ""
        self.set_busy(True, "토론 준비 중...")
        await self.on_send_message(text)

    async def _handle_stop(self) -> None:
        if not self.is_busy or self.on_stop is None or self._stop_pending:
            return
        # 요청이 반영되기까지 진행 중인 발언 하나가 남아 있을 수 있습니다.
        # 그동안 버튼을 눌러 봐야 할 일이 없으므로 잠가 둡니다.
        self.set_stop_pending(True)
        await self.on_stop()

    async def _handle_enter(self, e) -> None:
        await self._handle_send()

    def set_busy(self, busy: bool, status_text: str = "", round_info: str = "") -> None:
        if not self.alive:
            return
        # 문구가 바뀌었다는 것은 발언자나 단계가 넘어갔다는 뜻입니다. 경과 시간은
        # 턴 전체가 아니라 **지금 이 단계**에서 얼마나 기다렸는지를 세야, 한
        # 에이전트가 붙잡고 있는 상황이 눈에 보입니다.
        if busy and (not self.is_busy or (status_text and status_text != self._status_text)):
            self._busy_since = time.monotonic()
        if not busy:
            self._busy_since = None
        if status_text:
            self._status_text = status_text

        self.is_busy = busy
        # 개입 통로가 연결돼 있으면 토론 중에도 입력을 열어 둡니다. 그때 보낸 글은
        # 새 턴이 아니라 진행 중인 토론으로 들어갑니다.
        can_interject = busy and self.on_interject is not None
        if self.send_button:
            self.send_button.disable() if (busy and not can_interject) else self.send_button.enable()
            self.send_button.props(
                "icon=bolt color=amber-7" if can_interject else "icon=send color=indigo-6"
            )
        if self.input_field:
            self.input_field.disable() if (busy and not can_interject) else self.input_field.enable()
            self.input_field.props(
                f'placeholder="{INTERJECT_PLACEHOLDER if can_interject else IDLE_PLACEHOLDER}"'
            )
        if self.stop_button:
            self.stop_button.set_visibility(busy and self.on_stop is not None)
        if not busy:
            self.set_stop_pending(False)
        if self.status_spinner:
            self.status_spinner.set_visibility(busy)
        if self.status_label and status_text:
            self.status_label.set_text(status_text)
        if self.round_badge and round_info:
            self.round_badge.set_text(round_info)
        self._refresh_live_indicator()

    # ------------------------------------------------------------ 생성 중 표시

    def _refresh_live_indicator(self) -> None:
        """'지금 돌고 있다' 를 화면 세 곳에 반영합니다.

        같은 사실을 세 번 말하는 것은 자리마다 보이는 조건이 다르기 때문입니다.
        상태 막대는 늘 보이고, 진행 막대는 곁눈으로도 움직임이 잡히고, 아래쪽
        생성 중 줄은 사람이 실제로 글을 읽고 있는 자리에 붙습니다.
        """
        if not self.alive:
            return
        busy = self.is_busy

        if self.status_bar is not None and not self.status_bar.is_deleted:
            if busy:
                self.status_bar.classes(add="feed-status-live")
            else:
                self.status_bar.classes(remove="feed-status-live")
        if self.status_label is not None and not self.status_label.is_deleted:
            # 대기 중일 때까지 밝게 두면 정작 도는 중임을 알리지 못합니다.
            self.status_label.classes(
                replace="font-semibold truncate min-w-0 "
                        + ("text-sm text-indigo-100" if busy else "text-xs text-slate-300")
            )
        for element in (self.progress_bar, self.live_strip, self.elapsed_badge):
            if element is not None and not element.is_deleted:
                element.set_visibility(busy)
        if self.live_label is not None and not self.live_label.is_deleted:
            self.live_label.set_text(self._status_text or "응답을 기다리는 중...")

        self._tick_elapsed()
        self._refresh_follow_button()

    def _tick_elapsed(self) -> None:
        """경과 시간을 1초마다 갱신합니다.

        서버 이벤트에 기대지 않는 것이 핵심입니다. 도구 하나가 30초를 잡아먹는
        동안에는 이벤트가 하나도 오지 않고, 그 침묵이 바로 사람이 "멈췄다" 고
        느끼는 구간입니다. 시계는 그동안에도 움직입니다.
        """
        if not self.alive:
            return
        if self._busy_since is None:
            text = ""
        else:
            seconds = int(time.monotonic() - self._busy_since)
            text = f"{seconds}초" if seconds < 60 else f"{seconds // 60}분 {seconds % 60}초"
        for label in (self.elapsed_badge, self.live_elapsed):
            if label is not None and not label.is_deleted:
                label.set_text(text)

    def restore_input(self, text: str) -> None:
        """보내지 못한 입력을 입력칸에 되돌려 놓습니다.

        개입을 전달하려는 순간 토론이 막 끝나 있으면 글이 갈 곳을 잃습니다.
        사용자가 쓴 것을 삼키지 않도록 되돌립니다 (그 사이에 새로 쓴 글이 있으면
        건드리지 않습니다).
        """
        if not self.alive or self.input_field is None:
            return
        if (self.input_field.value or "").strip():
            return
        self.input_field.value = text

    def set_stop_pending(self, pending: bool) -> None:
        """정지 요청이 접수돼 마지막 발언과 합성을 기다리는 중임을 표시합니다."""
        self._stop_pending = pending
        if not self.alive or self.stop_button is None:
            return
        self.stop_button.set_text("정지 중..." if pending else "정지")
        self.stop_button.disable() if pending else self.stop_button.enable()

    # ------------------------------------------------------------------ 렌더링

    def clear(self) -> None:
        self._active_streams.clear()
        self._stop_pending = False
        # 카드가 사라지므로 펼침 기억도 함께 버립니다. 남겨 두면 다음 대화가
        # 있지도 않은 카드 때문에 자동 스크롤이 꺼진 채로 시작합니다.
        self._user_expanded.clear()
        self._refresh_follow_button()
        if not self.alive:
            return
        self._placeholder = None
        self.message_container.clear()
        with self.message_container:
            self._render_empty_placeholder()

    def render_all(
        self,
        messages: List[Dict[str, Any]],
        streaming_ids: Optional[Iterable[str]] = None,
    ) -> None:
        """피드를 통째로 다시 그립니다.

        `streaming_ids` 는 아직 생성 중인 발언입니다. 새로고침 뒤에 다시 붙었을 때
        이어지는 청크가 갈 곳을 만들어 두기 위해 스트리밍 카드로 등록해 둡니다.
        """
        if not self.alive:
            return
        streaming: Set[str] = set(streaming_ids or ())
        self._active_streams.clear()
        self._user_expanded.clear()
        self._refresh_follow_button()
        self._placeholder = None
        self.message_container.clear()

        if not messages:
            with self.message_container:
                self._render_empty_placeholder()
            return

        with self.message_container:
            for m in messages:
                msg_id = m.get("id", "")
                is_streaming = bool(msg_id) and msg_id in streaming
                handles = self._render_card(m, streaming=is_streaming)
                if is_streaming:
                    self._active_streams[msg_id] = handles

        # 방금 그린 화면입니다. 펼친 카드가 있을 수 없으므로 그냥 맨 아래로.
        self._scroll_to_bottom(force=True)

    def start_streaming_message(self, msg: Dict[str, Any]) -> None:
        """Starts a streaming message card in the feed."""
        if not self.alive:
            return
        msg_id = msg.get("id", "")
        if not msg_id or msg_id in self._active_streams:
            return

        self._drop_placeholder()
        with self.message_container:
            self._active_streams[msg_id] = self._render_card(msg, streaming=True)
        self._scroll_to_bottom()

    def append_stream_chunk(self, msg_id: str, delta: str) -> None:
        """Appends a text chunk to an actively streaming message card."""
        if not self.alive:
            return
        info = self._active_streams.get(msg_id)
        if not info:
            return
        info["content"] += delta
        info["markdown"].set_content(info["content"])
        self._scroll_to_bottom()

    def append_message(self, msg: Dict[str, Any]) -> None:
        if not self.alive:
            return

        msg_id = msg.get("id", "")
        if msg_id and msg_id in self._active_streams:
            self._finalize_streaming_message(msg)
            return

        self._drop_placeholder()
        with self.message_container:
            self._render_card(msg)
        self._scroll_to_bottom()

    def _finalize_streaming_message(self, msg: Dict[str, Any]) -> None:
        """스트리밍 카드를 최종 내용으로 확정합니다.

        연결이 끊기면 발언이 실패 안내(`msg_type="error"`)로 바뀝니다. 이미 그려진
        카드의 색과 배지도 그때 함께 바꿔야, 지어낸 발언처럼 보이지 않습니다.
        """
        msg_id = msg.get("id", "")
        info = self._active_streams.pop(msg_id, None)
        if info is None:
            self.append_message(msg)
            return

        final_content = msg.get("content", info["content"])
        info["markdown"].set_content(final_content)
        info["content"] = final_content

        msg_type = msg.get("msg_type", "agent")
        if msg_type != info.get("msg_type"):
            card = info.get("card")
            if card is not None and not card.is_deleted:
                card.classes(
                    replace=f"w-full p-3.5 rounded-xl border "
                            f"{_card_classes(msg_type, msg.get('sender_key', ''))} shadow-md"
                )
            failed_badge = info.get("failed_badge")
            if failed_badge is not None and not failed_badge.is_deleted:
                failed_badge.set_visibility(msg_type == "error")

        tool_calls = msg.get("tool_calls", [])
        if tool_calls and info.get("tool_container") is not None:
            with info["tool_container"]:
                for tc in tool_calls:
                    self._render_tool_accordion(tc)

        # 발언이 끝났으므로 접습니다. 여기가 이 기능이 실제로 걸리는 자리입니다 —
        # 그리는 동안에는 펼쳐 두고, 다 쓰고 나면 세 줄로 줄입니다. 단, 사람이
        # 직접 펼쳐 둔 카드는 건드리지 않습니다. 읽고 있는 글을 화면이 접어
        # 버리면 그것대로 남의 손이 끼어든 것입니다.
        if (info.get("id") or "") not in self._user_expanded:
            self._apply_clamp(info, collapsed=True)
        self._scroll_to_bottom()

    def _drop_placeholder(self) -> None:
        """빈 상태 안내가 떠 있으면 치웁니다."""
        placeholder = self._placeholder
        if placeholder is None or placeholder.is_deleted:
            self._placeholder = None
            return
        self.message_container.remove(placeholder)
        self._placeholder = None

    # ------------------------------------------------------------ 스크롤 따라가기

    @property
    def following(self) -> bool:
        """새 발언을 따라 아래로 내려갈지.

        펼쳐 둔 카드가 하나라도 있으면 따라가지 않습니다. 카드를 편 이유는 그것을
        읽기 위해서인데, 그동안에도 다른 에이전트의 글자는 계속 도착합니다. 매
        청크마다 맨 아래로 끌려가면 읽던 자리를 잃고, 다시 올라가면 곧바로 또
        끌려 내려갑니다. 전부 접혀 있을 때는 볼 것이 흐름뿐이므로 따라갑니다.
        """
        return not self._user_expanded

    def _scroll_to_bottom(self, force: bool = False) -> None:
        """맨 아래로. `force` 는 사람이 직접 요청했거나 화면을 새로 그린 경우입니다."""
        if not force and not self.following:
            return
        if self.scroll_area is not None and not self.scroll_area.is_deleted:
            self.scroll_area.scroll_to(percent=1.0)

    def _refresh_follow_button(self) -> None:
        """자동 스크롤이 멈춰 있다는 사실과 되돌아갈 방법을 함께 보여줍니다.

        말없이 멈추면 이번에는 "스크롤이 고장 났다" 가 됩니다.
        """
        if self.follow_button is None or self.follow_button.is_deleted:
            return
        paused = bool(self._user_expanded)
        self.follow_button.set_visibility(paused)
        if paused:
            self.follow_button.set_text(f"맨 아래로 ({len(self._user_expanded)}개 펼침)")

    def _handle_follow(self) -> None:
        """멈춰 있는 동안에도 한 번은 맨 아래를 볼 수 있어야 합니다.

        카드를 대신 접지는 않습니다. 사람이 편 것을 화면이 마음대로 접으면,
        읽으려던 글이 사라집니다.
        """
        self._scroll_to_bottom(force=True)

    def _render_card(self, msg: Dict[str, Any], streaming: bool = False) -> Dict[str, Any]:
        """발언 카드 하나. 스트리밍 중이든 확정된 것이든 같은 모양입니다.

        `streaming=True` 면 펼친 채로 둡니다. 생성되는 글을 지켜보는 것이 이 화면의
        핵심인데, 쓰이는 도중에 접어 버리면 세 줄만 계속 갈아 끼우는 꼴이 됩니다.
        """
        sender_key = msg.get("sender_key", "agent")
        sender_name = msg.get("sender_name", "Agent")
        sender_role = msg.get("sender_role", "")
        content = msg.get("content", "")
        msg_type = msg.get("msg_type", "agent")
        round_num = msg.get("round_number", 0)
        tool_calls = msg.get("tool_calls", [])

        # conf.json 에 새로 추가한 에이전트는 이 표에 없습니다. 예전에는 그때
        # 사용자 스타일로 떨어져, 에이전트 발언이 사용자 말풍선처럼 보였습니다.
        style = style_for_agent(sender_key)

        # 버튼 핸들러가 이 딕셔너리를 통해 카드 상태를 봅니다. 카드를 다 그린 뒤에
        # 채우지만, 핸들러는 클릭될 때 읽으므로 지금 비어 있어도 됩니다.
        info: Dict[str, Any] = {}

        with ui.card().classes(
            f"w-full p-3.5 rounded-xl border {_card_classes(msg_type, sender_key)} shadow-md"
        ) as card:
            with ui.row().classes("w-full items-center justify-between mb-1.5"):
                with ui.row().classes("items-center gap-2"):
                    ui.avatar(style["avatar"], color=style["color"], text_color="white", size="sm")
                    with ui.column().classes("gap-0"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(sender_name).classes("text-sm font-bold text-slate-100")
                            if sender_role:
                                ui.badge(sender_role, color=style["color"]).props("dense text-[10px]")
                            failed_badge = ui.badge("응답 없음", color="red-9").props("dense text-[10px]")
                            failed_badge.set_visibility(msg_type == "error")

                with ui.row().classes("items-center gap-1 no-wrap flex-shrink-0"):
                    if round_num > 0:
                        ui.badge(f"Round {round_num}", color="slate-700").props("dense text-[10px]")
                    ui.button(
                        icon="content_copy", on_click=lambda: self._copy_card(info)
                    ).props("flat dense round size=sm color=slate-4").tooltip(
                        "이 발언을 클립보드에 복사"
                    )
                    expand_btn = ui.button(
                        icon="unfold_more", on_click=lambda: self._toggle_card(info)
                    ).props("flat dense round size=sm color=slate-4")
                    # 툴팁은 여기서 한 번만 만들고 이후에는 문구만 갈아 끼웁니다.
                    # 접힘 상태가 바뀔 때마다 `tooltip()` 을 부르면 그때의 슬롯에
                    # 새 q-tooltip 이 쌓입니다.
                    with expand_btn:
                        expand_tip = ui.tooltip("")

            body = ui.column().classes(
                "w-full prose prose-invert max-w-none text-sm text-slate-200"
            )
            with body:
                md = ui.markdown(content)

            tool_container = ui.column().classes("w-full mt-2 gap-1")
            if tool_calls:
                with tool_container:
                    for tc in tool_calls:
                        self._render_tool_accordion(tc)

        info.update({
            # 펼쳐 둔 카드를 세려면 카드마다 이름표가 있어야 합니다.
            "id": msg.get("id", ""),
            "card": card,
            "markdown": md,
            "content": content,
            "body": body,
            "expand_btn": expand_btn,
            "expand_tip": expand_tip,
            "tool_container": tool_container,
            "failed_badge": failed_badge,
            "msg_type": msg_type,
            "collapsed": False,
        })
        self._apply_clamp(info, collapsed=not streaming)
        return info

    # ------------------------------------------------------------ 접기 / 복사

    def _apply_clamp(self, info: Dict[str, Any], collapsed: bool) -> None:
        """카드를 접거나 폅니다.

        접을 것이 없는 짧은 발언에는 펼치기 버튼 자체를 달지 않습니다. 한 줄짜리
        발언마다 아무 일도 하지 않는 버튼이 붙으면 그게 더 거슬립니다.

        도구 호출 기록도 함께 감춥니다. 본문만 세 줄로 줄이고 아코디언 다섯 개가
        그대로 남으면 접은 보람이 없습니다.
        """
        clampable = is_clampable(info.get("content", ""))
        collapsed = collapsed and clampable

        expand_btn = info.get("expand_btn")
        if expand_btn is not None and not expand_btn.is_deleted:
            expand_btn.set_visibility(clampable)
            expand_btn.props(f"icon={'unfold_more' if collapsed else 'unfold_less'}")
        tip = info.get("expand_tip")
        if tip is not None and not tip.is_deleted:
            tip.set_text("펼치기" if collapsed else f"{CLAMP_LINES}줄만 보기")

        body = info.get("body")
        if body is not None and not body.is_deleted:
            if collapsed:
                body.classes(add="chat-body-clamped")
            else:
                body.classes(remove="chat-body-clamped")

        tool_container = info.get("tool_container")
        if tool_container is not None and not tool_container.is_deleted:
            tool_container.set_visibility(not collapsed)

        info["collapsed"] = collapsed

    def _toggle_card(self, info: Dict[str, Any]) -> None:
        """사람이 펼치기/접기를 누른 자리. 자동 스크롤이 켜지고 꺼지는 곳입니다."""
        collapsed = not info.get("collapsed", False)
        self._apply_clamp(info, collapsed=collapsed)

        msg_id = info.get("id") or ""
        # 접을 것이 없는 짧은 발언은 세지 않습니다. 펼침 버튼도 달리지 않으므로
        # 여기 올 일이 없지만, 온다면 그건 "읽는 중" 이 아니라 빈 토글입니다.
        if not msg_id or not is_clampable(info.get("content", "")):
            return
        if collapsed:
            self._user_expanded.discard(msg_id)
        else:
            self._user_expanded.add(msg_id)

        self._refresh_follow_button()
        # 마지막 펼침을 접었다면 다시 흐름을 따라가겠다는 뜻입니다. 생성 중일
        # 때만 곧바로 내려갑니다 — 멈춰 있는 화면을 임의로 움직이지 않습니다.
        if self.following and self._active_streams:
            self._scroll_to_bottom()

    def _copy_card(self, info: Dict[str, Any]) -> None:
        """이 발언을 클립보드에 넣습니다.

        화면에 그려진 결과가 아니라 마크다운 원문입니다. 이 글을 다시 쓸 곳은
        대부분 마크다운을 읽는 곳이고, 접혀 있어도 전문이 그대로 갑니다.
        """
        text = info.get("content", "")
        if not text.strip():
            ui.notify("복사할 내용이 없습니다.", type="warning", position="bottom-right")
            return
        copy_to_clipboard(text)
        ui.notify("클립보드에 복사했습니다.", type="positive", position="bottom-right")

    def _render_tool_accordion(self, tc: Dict[str, Any]) -> None:
        tool_name = tc.get("tool_name", "unknown_tool")
        status = tc.get("status", "success")
        args = tc.get("arguments", {})
        output = tc.get("output", "")

        status_color = "teal-4" if status == "success" else "red-4"
        with ui.expansion(f"🛠️ Tool Call: {tool_name}", icon="build").classes("w-full mcp-tool-accordion text-xs"):
            with ui.column().classes("p-2 gap-2 bg-slate-950/60 rounded"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("Status:").classes("font-semibold text-slate-400")
                    ui.badge(status.upper(), color=status_color).props("dense")

                ui.label("Arguments:").classes("font-semibold text-slate-400 mt-1")
                args_str = json.dumps(args, indent=2, ensure_ascii=False) if isinstance(args, dict) else str(args)
                ui.code(args_str, language="json").classes("w-full text-xs")

                ui.label("Execution Output:").classes("font-semibold text-slate-400 mt-1")
                with ui.scroll_area().classes("w-full max-h-32 bg-black/40 p-2 rounded text-slate-300 font-mono text-[11px]"):
                    ui.label(output)

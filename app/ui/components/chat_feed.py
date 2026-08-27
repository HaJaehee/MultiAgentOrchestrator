import json
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional
from nicegui import ui
from app.agents.base import AGENT_STYLE_MAP

logger = logging.getLogger(__name__)


class ChatFeed:
    """Interactive multi-agent debate timeline with styled chat bubbles and collapsible MCP tool logs."""

    def __init__(self, on_send_message: Callable[[str], Coroutine[None, None, None]]):
        self.on_send_message = on_send_message
        self.scroll_area: Optional[ui.scroll_area] = None
        self.message_container: Optional[ui.column] = None
        self.status_bar: Optional[ui.row] = None
        self.status_label: Optional[ui.label] = None
        self.status_spinner: Optional[ui.spinner] = None
        self.input_field: Optional[ui.input] = None
        self.send_button: Optional[ui.button] = None
        self.is_busy: bool = False
        self._active_streams: Dict[str, Dict[str, Any]] = {}

    def build_ui(self) -> ui.column:
        # flex-nowrap 없이는 내용이 카드보다 커질 때 입력 바가 다음 열로 줄바꿈되어
        # 화면 밖으로 밀려납니다 (NiceGUI 컬럼 기본값이 flex-wrap: wrap).
        with ui.column().classes(
            "w-full h-full flex flex-col flex-nowrap justify-between overflow-hidden"
        ) as root:
            # 1. Status Indicator Bar
            with ui.row().classes(
                "w-full items-center justify-between px-3 py-1.5 bg-slate-900/90 border border-slate-800 rounded-lg text-xs flex-shrink-0"
            ) as self.status_bar:
                with ui.row().classes("items-center gap-2"):
                    self.status_spinner = ui.spinner("dots", size="sm", color="indigo-4")
                    self.status_spinner.set_visibility(False)
                    self.status_label = ui.label("대기 중 (Ready for prompt)").classes("font-semibold text-slate-300")
                self.round_badge = ui.badge("Ready", color="slate-700").props("dense")

            # 2. Scrollable Messages Timeline (Fills all remaining vertical space)
            with ui.scroll_area().classes("w-full flex-grow my-2 pr-2 min-h-0") as self.scroll_area:
                self.message_container = ui.column().classes("w-full gap-3 debate-timeline")
                with self.message_container:
                    self._render_empty_placeholder()

            # 3. Input Bar
            with ui.row().classes("w-full items-center gap-2 p-2 bg-slate-900 border border-slate-800 rounded-xl shadow-lg flex-shrink-0"):
                self.input_field = ui.input(
                    placeholder="멀티 에이전트에게 토론 및 설계를 요청할 목표/질문을 입력하세요...",
                ).props("outlined dark dense autogrow").classes("flex-grow text-sm").on("keydown.enter", self._handle_enter)

                self.send_button = ui.button(
                    icon="send",
                    on_click=self._handle_send,
                ).props("unelevated color=indigo-6 round")

        return root

    def _render_empty_placeholder(self) -> None:
        with ui.column().classes("w-full items-center justify-center py-16 text-center text-slate-500"):
            ui.icon("forum", size="xl").classes("text-slate-700 mb-2")
            ui.label("새로운 토론을 시작해 보세요").classes("text-base font-bold text-slate-400")
            ui.label("유저 요청을 입력하면 Master Orchestrator가 목표를 분해하고 전문가 토론을 진행합니다.").classes("text-xs max-w-md")

    async def _handle_send(self) -> None:
        if self.is_busy or not self.input_field:
            return
        text = (self.input_field.value or "").strip()
        if not text:
            return
        self.input_field.value = ""
        self.set_busy(True, "토론 준비 중...")
        await self.on_send_message(text)

    async def _handle_enter(self, e) -> None:
        await self._handle_send()

    def set_busy(self, busy: bool, status_text: str = "", round_info: str = "") -> None:
        self.is_busy = busy
        if self.send_button:
            self.send_button.disable() if busy else self.send_button.enable()
        if self.input_field:
            self.input_field.disable() if busy else self.input_field.enable()
        if self.status_spinner:
            self.status_spinner.set_visibility(busy)
        if self.status_label and status_text:
            self.status_label.set_text(status_text)
        if hasattr(self, "round_badge") and self.round_badge and round_info:
            self.round_badge.set_text(round_info)

    def clear(self) -> None:
        self._active_streams.clear()
        if self.message_container:
            self.message_container.clear()
            with self.message_container:
                self._render_empty_placeholder()

    def start_streaming_message(self, msg: Dict[str, Any]) -> None:
        """Starts a streaming message card in the feed."""
        if not self.message_container:
            return
        msg_id = msg.get("id", "")
        if not msg_id:
            return

        # If placeholder was present, remove it
        if len(self.message_container.default_slot.children) == 1 and not hasattr(msg, "get"):
            self.message_container.clear()

        if msg_id in self._active_streams:
            return

        with self.message_container:
            info = self._render_streaming_card(msg)
            self._active_streams[msg_id] = info

        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    def append_stream_chunk(self, msg_id: str, delta: str) -> None:
        """Appends a text chunk to an actively streaming message card."""
        info = self._active_streams.get(msg_id)
        if not info:
            return
        info["content"] += delta
        info["markdown"].set_content(info["content"])
        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    def finalize_streaming_message(self, msg: Dict[str, Any]) -> None:
        """Finalizes a streaming card, updating final content and adding tool calls if present."""
        msg_id = msg.get("id", "")
        info = self._active_streams.pop(msg_id, None)
        if info:
            final_content = msg.get("content", info["content"])
            info["markdown"].set_content(final_content)
            tool_calls = msg.get("tool_calls", [])
            if tool_calls and info.get("tool_container"):
                with info["tool_container"]:
                    for tc in tool_calls:
                        self._render_tool_accordion(tc)
        else:
            self.append_message(msg)

        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    def append_message(self, msg: Dict[str, Any]) -> None:
        if not self.message_container:
            return

        msg_id = msg.get("id", "")
        if msg_id and msg_id in self._active_streams:
            self.finalize_streaming_message(msg)
            return

        # If placeholder was present, remove it
        if len(self.message_container.default_slot.children) == 1 and not hasattr(msg, "get"):
            self.message_container.clear()

        with self.message_container:
            self._render_message_card(msg)

        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    def render_all(self, messages: List[Dict[str, Any]]) -> None:
        if not self.message_container:
            return
        self._active_streams.clear()
        self.message_container.clear()
        if not messages:
            with self.message_container:
                self._render_empty_placeholder()
            return

        with self.message_container:
            for m in messages:
                self._render_message_card(m)

        if self.scroll_area:
            self.scroll_area.scroll_to(percent=1.0)

    def _render_streaming_card(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        sender_key = msg.get("sender_key", "agent")
        sender_name = msg.get("sender_name", "Agent")
        sender_role = msg.get("sender_role", "")
        content = msg.get("content", "")
        msg_type = msg.get("msg_type", "agent")
        round_num = msg.get("round_number", 0)

        style = AGENT_STYLE_MAP.get(sender_key, AGENT_STYLE_MAP["user"])
        avatar_icon = style["avatar"]

        is_user = (msg_type == "user" or sender_key == "user")
        is_orchestrator = (msg_type == "orchestrator" or sender_key == "orchestrator")

        card_bg = (
            "bg-blue-950/40 border-blue-800/60" if is_user
            else ("bg-indigo-950/50 border-indigo-500/70" if is_orchestrator else "bg-slate-900/90 border-slate-800")
        )

        with ui.card().classes(f"w-full p-3.5 rounded-xl border {card_bg} shadow-md") as card:
            with ui.row().classes("w-full items-center justify-between mb-1.5"):
                with ui.row().classes("items-center gap-2"):
                    ui.avatar(avatar_icon, color=style["color"], text_color="white", size="sm")
                    with ui.column().classes("gap-0"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(sender_name).classes("text-sm font-bold text-slate-100")
                            if sender_role:
                                ui.badge(sender_role, color=style["color"]).props("dense text-[10px]")

                if round_num > 0:
                    ui.badge(f"Round {round_num}", color="slate-700").props("dense text-[10px]")

            with ui.column().classes("w-full prose prose-invert max-w-none text-sm text-slate-200"):
                md = ui.markdown(content)

            tool_container = ui.column().classes("w-full mt-2 gap-1")

        return {"card": card, "markdown": md, "content": content, "tool_container": tool_container}

    def _render_message_card(self, msg: Dict[str, Any]) -> None:
        sender_key = msg.get("sender_key", "agent")
        sender_name = msg.get("sender_name", "Agent")
        sender_role = msg.get("sender_role", "")
        content = msg.get("content", "")
        msg_type = msg.get("msg_type", "agent")
        round_num = msg.get("round_number", 0)
        tool_calls = msg.get("tool_calls", [])

        style = AGENT_STYLE_MAP.get(sender_key, AGENT_STYLE_MAP["user"])
        avatar_icon = style["avatar"]

        is_user = (msg_type == "user" or sender_key == "user")
        is_orchestrator = (msg_type == "orchestrator" or sender_key == "orchestrator")

        card_bg = (
            "bg-blue-950/40 border-blue-800/60" if is_user
            else ("bg-indigo-950/50 border-indigo-500/70" if is_orchestrator else "bg-slate-900/90 border-slate-800")
        )

        with ui.card().classes(f"w-full p-3.5 rounded-xl border {card_bg} shadow-md"):
            # Header
            with ui.row().classes("w-full items-center justify-between mb-1.5"):
                with ui.row().classes("items-center gap-2"):
                    ui.avatar(avatar_icon, color=style["color"], text_color="white", size="sm")
                    with ui.column().classes("gap-0"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(sender_name).classes("text-sm font-bold text-slate-100")
                            if sender_role:
                                ui.badge(sender_role, color=style["color"]).props("dense text-[10px]")
                
                # Round Badge
                if round_num > 0:
                    ui.badge(f"Round {round_num}", color="slate-700").props("dense text-[10px]")

            # Body (Markdown)
            with ui.column().classes("w-full prose prose-invert max-w-none text-sm text-slate-200"):
                ui.markdown(content)

            # Collapsible MCP Tool Calls
            if tool_calls:
                with ui.column().classes("w-full mt-2 gap-1"):
                    for tc in tool_calls:
                        self._render_tool_accordion(tc)

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

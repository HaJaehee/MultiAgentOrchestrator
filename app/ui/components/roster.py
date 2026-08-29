from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional
from nicegui import ui
from app.agents.base import Agent
from app.agents.pool import AgentPool, get_agent_pool, reload_agent_pool
from app.config import (
    active_config_path,
    add_mcp_server_to_conf_file,
    get_config,
    remove_mcp_server_from_conf_file,
    resolve_workspace_dir,
    set_agent_allowed_mcp_servers_in_conf_file,
    set_mcp_server_enabled_in_conf_file,
)
from app.mcp.manager import get_mcp_manager
from app.orchestration.runner import get_debate_runner
from app.orchestration.strategies import STRATEGY_MAP

logger = logging.getLogger(__name__)

# 진행 토스트가 스스로 사라지는 시간(초). 서버 재기동은 보통 2~6초면 끝나므로
# 그 뒤에는 결과 토스트가 대신 말해 줍니다. 사라지는 것을 보장하는 것은 이 값이고,
# 작업이 끝났을 때 부르는 dismiss() 는 더 일찍 치우기 위한 것입니다.
PROGRESS_TOAST_TIMEOUT = 8


class AgentRosterControl:
    """Agent selection cards and debate control panel (rounds, strategy, instructions)."""

    def __init__(
        self,
        on_config_changed: Optional[Callable[[], Coroutine[None, None, None]]] = None,
    ):
        self.on_config_changed = on_config_changed
        self.agent_pool: AgentPool = get_agent_pool()
        self.selected_agents: Dict[str, bool] = {}
        self.strategy_name: str = "free_debate"
        self.max_rounds: int = 3
        self.custom_instructions: str = ""
        # 이 대화의 작업 공간. 빈 문자열이면 conf.toml 기본값을 씁니다.
        self.workspace_dir: str = ""

        # Init selection defaults
        for ag in self.agent_pool.list_all():
            self.selected_agents[ag.key] = True

        self.strategy_select: Optional[ui.select] = None
        self.rounds_slider: Optional[ui.slider] = None
        self.custom_instr_input: Optional[ui.textarea] = None
        self.expansion: Optional[ui.expansion] = None
        self.summary_badge: Optional[ui.badge] = None
        self.session_id: Optional[str] = None
        self.personas_locked: bool = False
        self.persona_button: Optional[ui.button] = None
        self.reload_conf_btn: Optional[ui.button] = None
        self.persona_tooltip: Optional[ui.tooltip] = None
        self.persona_badge: Optional[ui.badge] = None
        self.mcp_row: Optional[ui.row] = None
        self.mcp_badge: Optional[ui.badge] = None
        self.mcp_reconnect_btn: Optional[ui.button] = None
        self.mcp_add_btn: Optional[ui.button] = None
        self.mcp_lock_badge: Optional[ui.badge] = None
        self.mcp_lock_hint: Optional[ui.label] = None
        # MCP 구성은 프로세스 전체가 공유합니다. 토론이 하나라도 돌고 있으면
        # 그 도구가 지금 쓰이는 중이므로 잠급니다.
        self.mcp_locked: bool = False
        self.mcp_lock_reason: str = ""
        self.cards_row: Optional[ui.row] = None
        self.workspace_input: Optional[ui.input] = None
        self.workspace_hint: Optional[ui.label] = None
        self.workspace_apply_btn: Optional[ui.button] = None
        self.current_personas: Optional[Dict[str, Any]] = None

    @property
    def alive(self) -> bool:
        """이 로스터가 아직 살아 있는 페이지에 붙어 있는지.

        토론이 백그라운드로 옮겨 가면서, 이미 버려진 화면의 컨트롤을 갱신하려는
        호출이 생길 수 있습니다. 그럴 때 조용히 넘어갑니다.
        """
        return self.expansion is not None and not self.expansion.is_deleted

    def build_ui(self) -> ui.expansion:
        # Expansion defaulted to open as requested
        self.expansion = ui.expansion(
            "⚙️ 에이전트 풀, 도구, 토론 설정",
            icon="tune",
            value=True,  # Expanded by default
        ).classes("w-full bg-slate-900 border border-slate-800 rounded-xl shadow-md text-slate-100 flex-shrink-0")

        with self.expansion:
            # 펼친 상태에서도 아래 채팅창이 보이도록 높이를 제한합니다. 고정 비율 대신
            # "채팅이 필요한 높이를 뺀 나머지" 로 잡아, 화면이 크면 설정이 전부 보이고
            # 작을 때만 내부에서 스크롤되게 합니다.
            with ui.column().classes(
                "w-full p-2 gap-3 max-h-[calc(100vh-400px)] overflow-y-auto"
            ):
                # 1. Agent Selection Cards
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("토론 참여 에이전트 선택").classes("text-xs font-bold text-slate-300")
                        self.persona_badge = ui.badge("고정됨", color="amber-8").props("dense text-[9px]")
                        self.persona_badge.set_visibility(False)
                    with ui.row().classes("items-center gap-2"):
                        self.reload_conf_btn = (
                            ui.button("conf.toml 다시 읽기", icon="sync",
                                      on_click=self._on_reload_conf)
                            .props("flat dense no-caps color=teal-4")
                            .classes("text-[11px]")
                        )
                        self.reload_conf_btn.tooltip(
                            "앱을 다시 띄우지 않고 conf.toml 을 다시 읽어 에이전트 목록과 "
                            "모델·도구 설정을 갱신합니다"
                        )
                        self.persona_button = (
                            ui.button("페르소나 편집", icon="badge", on_click=self._open_persona_editor)
                            .props("flat dense color=indigo-4")
                            .classes("text-[11px]")
                        )
                        # 툴팁은 여기서 딱 한 번 만듭니다. `Element.tooltip()` 은 호출할
                        # 때마다 "현재 슬롯" 에 새 q-tooltip 을 만드는데, 토론 종료
                        # 콜백처럼 페이지 밖 태스크에서 부르면 그 슬롯의 부모가 이미
                        # 지워져 있어 `The parent element this slot belongs to has been
                        # deleted.` 로 터졌습니다. 이후에는 텍스트만 갈아 끼웁니다.
                        with self.persona_button:
                            self.persona_tooltip = ui.tooltip("")
                        self.summary_badge = ui.badge("4 Agents Active", color="indigo-7").props("dense text-xs")
                self._refresh_persona_controls()

                self.cards_row = ui.row().classes("w-full gap-2 flex-wrap")
                with self.cards_row:
                    for ag in self.agent_pool.list_all():
                        self._build_agent_card(ag)

                ui.separator().classes("bg-slate-800 my-1")

                # 2. MCP Tool Servers — 상태 표시와 추가/삭제/on-off
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("MCP 서버").classes("text-xs font-bold text-slate-300")
                        self.mcp_badge = ui.badge("-", color="slate-7").props("dense text-xs")
                        self.mcp_lock_badge = ui.badge("잠김", color="amber-8").props("dense text-[9px]")
                        self.mcp_lock_badge.set_visibility(False)
                    with ui.row().classes("items-center gap-1"):
                        self.mcp_add_btn = (
                            ui.button("서버 추가", icon="add", on_click=self._open_mcp_add_dialog)
                            .props("flat dense no-caps size=sm color=indigo-4")
                            .classes("text-[11px]")
                        )
                        self.mcp_reconnect_btn = (
                            ui.button(icon="refresh", on_click=self._on_mcp_reconnect)
                            .props("flat dense round size=sm color=slate-4")
                        )
                        self.mcp_reconnect_btn.tooltip("연결되지 않은 MCP 서버 다시 시도")

                # 이 설정은 대화 하나의 것이 아닙니다. 작업 공간과 달리 conf.toml 에
                # 저장되고, MCP 서버는 프로세스 전체가 공유합니다.
                ui.label(
                    "⚠️ MCP 서버의 추가·삭제·on/off 는 conf.toml 에 저장되며, 지금 열려 있는 모든 "
                    "대화와 앞으로 만드는 모든 대화에 함께 적용됩니다."
                ).classes("text-[10px] text-amber-400/90 w-full leading-snug -mt-1")
                self.mcp_lock_hint = ui.label("").classes(
                    "text-[10px] text-rose-300/90 w-full leading-snug"
                )
                self.mcp_lock_hint.set_visibility(False)

                self.mcp_row = ui.row().classes("w-full gap-2 flex-wrap items-center")
                self._sync_mcp_lock()
                self.refresh_mcp_status()
                # 다른 화면에서 시작·종료된 토론은 이 화면에 이벤트로 오지 않습니다.
                # 잠금 상태만 주기적으로 맞춥니다 (딕셔너리 조회 한 번이고, 상태가
                # 바뀔 때만 다시 그립니다).
                ui.timer(2.0, self._sync_mcp_lock)

                # 작업 공간 (이 대화 전용). filesystem·git·memory·sandbox MCP 가
                # 모두 이 폴더 하나를 공유합니다.
                with ui.row().classes("w-full items-center gap-2 no-wrap mt-1"):
                    ui.icon("folder_open", size="xs").classes("text-amber-400")
                    ui.label("작업 공간:").classes("text-xs font-semibold text-slate-300 flex-shrink-0")
                    self.workspace_input = ui.input(
                        placeholder="비우면 기본값 (프로젝트 루트의 workspace)",
                        value=self.workspace_dir,
                    ).props("outlined dark dense").classes("flex-grow text-xs")
                    self.workspace_apply_btn = (
                        ui.button("적용", icon="check", on_click=self._on_workspace_apply)
                        .props("flat dense color=amber-4").classes("text-[11px] flex-shrink-0")
                    )
                    self.workspace_apply_btn.tooltip(
                        "이 대화에서 쓸 폴더로 MCP 서버를 다시 띄웁니다. conf.toml 은 바뀌지 않습니다"
                    )
                self.workspace_hint = ui.label("").classes(
                    "text-[10px] text-slate-500 truncate w-full"
                )
                self._refresh_workspace_hint()

                ui.separator().classes("bg-slate-800 my-1")

                # 3. Control Panel: Rounds & Strategy & Custom Instructions
                with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
                    # Strategy Selection
                    with ui.row().classes("items-center gap-2 min-w-[240px]"):
                        ui.icon("alt_route", size="xs").classes("text-indigo-400")
                        ui.label("토론 전략:").classes("text-xs font-semibold text-slate-300")
                        strategy_options = {k: v.display_name for k, v in STRATEGY_MAP.items()}
                        self.strategy_select = ui.select(
                            options=strategy_options,
                            value=self.strategy_name,
                            on_change=self._on_strategy_change,
                        ).props("outlined dense dark options-dense").classes("w-52 text-xs")

                    # Max Rounds Slider
                    with ui.row().classes("items-center gap-2 min-w-[180px]"):
                        ui.icon("repeat", size="xs").classes("text-amber-400")
                        ui.label("최대 라운드:").classes("text-xs font-semibold text-slate-300")
                        self.rounds_slider = ui.slider(
                            min=1, max=5, step=1, value=self.max_rounds, on_change=self._on_rounds_change
                        ).props("dark label color=indigo-4").classes("w-20")
                        self.rounds_label = ui.badge(str(self.max_rounds), color="indigo-7").props("dense")

                # Custom Instructions Input
                with ui.column().classes("w-full gap-1 mt-1"):
                    ui.label("세션 전용 커스텀 지침 (선택사항):").classes("text-[11px] font-semibold text-slate-400")
                    self.custom_instr_input = ui.textarea(
                        placeholder="이 세션에 특화된 추가 지침을 입력하세요 (예: '비동기 FastAPI 및 Pydantic v2 기준으로 작성')...",
                        value=self.custom_instructions,
                        on_change=self._on_instructions_change,
                    ).props("outlined dark dense autogrow rows=2").classes("w-full text-xs")

        return self.expansion

    def refresh_agent_cards(self, personas: Optional[Dict[str, Any]] = None) -> None:
        """에이전트 카드 목록을 현재 페르소나 및 풀 상태로 다시 그립니다."""
        if self.cards_row is None or self.cards_row.is_deleted:
            return
        if personas is not None:
            self.current_personas = personas
        self.agent_pool = get_agent_pool()
        self.cards_row.clear()
        with self.cards_row:
            for ag in self.agent_pool.list_all():
                p = self.current_personas.get(ag.key) if self.current_personas else None
                self._build_agent_card(ag, persona=p)

    def _build_agent_card(self, agent: Agent, persona: Optional[Any] = None) -> None:
        is_orchestrator = (agent.key == "orchestrator")
        is_active = self.selected_agents.get(agent.key, True)

        display_name = persona.name if (persona and getattr(persona, "name", None)) else agent.name
        display_role = persona.role if (persona and getattr(persona, "role", None)) else agent.role
        is_customized = getattr(persona, "is_customized", False) if persona else False

        card_cls = "p-2 rounded-lg border flex-grow max-w-[240px] min-w-[180px] transition-all "
        card_cls += "bg-slate-800/90 border-indigo-500/60" if is_active else "bg-slate-900/60 border-slate-800 opacity-50"

        with ui.card().classes(card_cls):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                with ui.row().classes("items-center gap-2 min-w-0"):
                    ui.avatar(agent.avatar, color=agent.color, text_color="white", size="xs")
                    with ui.column().classes("gap-0 min-w-0"):
                        with ui.row().classes("items-center gap-1 no-wrap"):
                            ui.label(display_name).classes("text-xs font-bold truncate")
                            if is_customized:
                                ui.badge("수정됨", color="indigo-7").props("dense text-[8px]")
                        ui.label(display_role).classes("text-[9px] text-slate-400 truncate")

                if is_orchestrator:
                    ui.badge("필수", color="indigo-9").props("dense text-[9px]")
                else:
                    ui.checkbox(
                        value=is_active,
                        on_change=lambda e, k=agent.key: self._on_agent_toggle(k, e.value),
                    ).props("dense dark color=indigo-4")

            # Model / endpoint / sequential-thinking summary (from conf.toml)
            with ui.row().classes("w-full items-center gap-1 mt-1 no-wrap"):
                ui.icon("smart_toy", size="10px").classes("text-slate-500")
                ui.label(agent.model).classes("text-[9px] text-slate-400 truncate max-w-[120px]")
                if agent.sequential_thinking.enabled:
                    ui.badge(f"ST·{agent.sequential_thinking.mode}", color="teal-9").props("dense text-[8px]")
                if not agent.is_live:
                    # 엔드포인트가 없으면 발언 차례에 "연결 끊김" 으로 기록됩니다.
                    ui.badge("미설정", color="red-9").props("dense text-[8px]")

            # 이 에이전트가 쓸 수 있는 MCP 서버. 어느 도구를 주느냐가 발언의 질을
            # 좌우하는데, 지금까지는 conf.toml 을 직접 고치는 수밖에 없었습니다.
            allowed = agent.allowed_mcp_servers or []
            with ui.row().classes("w-full items-center gap-1 mt-1 no-wrap"):
                tools_button = ui.button(
                    f"도구 {len(allowed)}",
                    icon="handyman",
                    on_click=lambda _, k=agent.key: self._open_agent_tools_dialog(k),
                ).props("flat dense no-caps size=sm color=teal-4").classes("text-[10px]")
                ui.label(", ".join(allowed) or "없음").classes(
                    "text-[9px] text-slate-500 truncate"
                )
                if self.mcp_locked:
                    tools_button.disable()
                    tools_button.tooltip(self.mcp_lock_reason)
                else:
                    tools_button.tooltip("이 에이전트가 호출할 수 있는 MCP 서버 고르기")

            ui.tooltip(
                f"model: {agent.model}\n"
                f"endpoint: {agent.endpoint_label}\n"
                f"temperature: {agent.temperature} / max_tokens: {agent.max_tokens}\n"
                f"sequential thinking: "
                f"{f'{agent.sequential_thinking.mode} (max {agent.sequential_thinking.max_steps} steps)' if agent.sequential_thinking.enabled else 'disabled'}\n"
                f"mcp: {', '.join(agent.allowed_mcp_servers) or '-'}"
            ).classes("whitespace-pre-line text-[10px]")

    def _open_persona_editor(self) -> None:
        if not self.session_id:
            ui.notify("먼저 세션을 선택하거나 새로 만드세요.", type="warning", position="bottom-right")
            return
        ui.navigate.to(f"/personas/{self.session_id}")

    def _refresh_persona_controls(self) -> None:
        """잠금 상태에 따라 버튼 문구와 뱃지를 갱신합니다."""
        if not self.alive:
            return
        if self.persona_badge and not self.persona_badge.is_deleted:
            self.persona_badge.set_visibility(self.personas_locked)
        if self.persona_button and not self.persona_button.is_deleted:
            if self.personas_locked:
                self.persona_button.set_text("페르소나 보기")
                self.persona_button.props("icon=lock")
                tip = "토론이 시작되어 고정되었습니다. 값은 확인할 수 있습니다."
            else:
                self.persona_button.set_text("페르소나 편집")
                self.persona_button.props("icon=badge")
                tip = "첫 메시지를 보내기 전까지 이름·역할·시스템 프롬프트를 수정할 수 있습니다"
            if self.persona_tooltip is not None and not self.persona_tooltip.is_deleted:
                self.persona_tooltip.set_text(tip)

    def set_personas_locked(self, locked: bool) -> None:
        self.personas_locked = locked
        self._refresh_persona_controls()

    def refresh_mcp_status(self) -> None:
        """conf.toml 의 MCP 서버별 연결 상태를 칩으로 다시 그립니다."""
        if self.mcp_row is None or self.mcp_row.is_deleted:
            return

        try:
            status = get_mcp_manager().connection_status()
            configured = get_config().mcp_servers
        except Exception as e:  # noqa: BLE001 - UI 는 설정 오류로 죽지 않아야 합니다
            logger.warning(f"Could not read MCP status: {e}")
            status, configured = {}, {}

        self.mcp_row.clear()
        connected_count = 0

        with self.mcp_row:
            if not configured:
                ui.label("conf.toml 에 등록된 MCP 서버가 없습니다.").classes("text-[10px] text-slate-500")
            for name, server_cfg in configured.items():
                info = status.get(name)

                if not server_cfg.enabled:
                    icon, icon_cls, color, detail = "toggle_off", "text-slate-500", "grey-8", "비활성"
                    tip = f"{name}: conf.toml 에서 enabled = false"
                elif info is None:
                    icon, icon_cls, color, detail = "help_outline", "text-slate-500", "grey-8", "미기동"
                    tip = f"{name}: 아직 기동되지 않았습니다"
                elif info["connected"]:
                    connected_count += 1
                    icon, icon_cls, color = "check_circle", "text-emerald-400", "green-8"
                    detail = f"툴 {info['tool_count']}"
                    tip = f"{name}: 연결됨\ncommand: {info['command']}\n등록된 툴: {info['tool_count']}개"
                elif info["available"]:
                    icon, icon_cls, color, detail = "sync_problem", "text-amber-400", "amber-9", "연결 끊김"
                    tip = f"{name}: 세션이 끊겼습니다. 다음 도구 호출 시 자동 재연결을 시도합니다."
                else:
                    icon, icon_cls, color, detail = "error", "text-rose-400", "red-9", "연결 실패"
                    tip = (f"{name}: 기동 실패\ncommand: {info['command']}\n"
                           f"{info.get('error') or '원인을 확인할 수 없습니다'}\n"
                           f"에이전트는 이 서버의 도구 없이 토론을 진행합니다.")

                with ui.element("div").classes(
                    "flex items-center gap-1 pl-2 pr-1 py-1 rounded-md bg-slate-800/70 border border-slate-700"
                ):
                    # 툴팁은 상태 부분에만 답니다. 스위치 위에서까지 뜨면 조작을 가립니다.
                    with ui.element("div").classes("flex items-center gap-1"):
                        ui.icon(icon, size="12px").classes(icon_cls)
                        ui.label(name).classes("text-[10px] font-semibold text-slate-200")
                        ui.badge(detail, color=color).props("dense")
                        ui.tooltip(tip).classes("whitespace-pre-line text-[10px]")

                    toggle = ui.switch(
                        value=server_cfg.enabled,
                        on_change=lambda e, n=name: self._on_mcp_toggle(n, bool(e.value)),
                    ).props("dense dark size=xs color=indigo-4").classes("ml-1")
                    remove = ui.button(
                        icon="delete_outline",
                        on_click=lambda _, n=name: self._open_mcp_delete_dialog(n),
                    ).props("flat dense round size=xs color=rose-4")
                    if self.mcp_locked:
                        toggle.disable()
                        remove.disable()
                        toggle.tooltip(self.mcp_lock_reason)
                        remove.tooltip(self.mcp_lock_reason)
                    else:
                        remove.tooltip(f"'{name}' 서버를 conf.toml 에서 삭제")

        enabled_total = len([c for c in configured.values() if c.enabled])
        if self.mcp_badge:
            summary_color = (
                "green-7" if enabled_total and connected_count == enabled_total
                else "amber-8" if connected_count
                else "red-8"
            )
            self.mcp_badge.set_text(f"{connected_count}/{enabled_total} 연결됨")
            self.mcp_badge.props(f"dense color={summary_color}")

    # ------------------------------------------------------------------ MCP 잠금

    def _current_mcp_lock_reason(self) -> str:
        """지금 MCP 구성을 바꾸면 안 되는 이유. 바꿔도 되면 빈 문자열.

        진행 중인 토론은 지금 이 서버들의 도구를 쓰고 있습니다. 도중에 서버를
        내리거나 다시 띄우면 그 토론의 도구 호출이 실패하거나, 더 나쁘게는 새로
        뜬 서버가 다른 구성으로 응답합니다. 이 대화든 다른 대화든 마찬가지입니다.
        """
        running = get_debate_runner().running_sessions()
        if not running:
            return ""
        return (
            f"토론이 진행 중인 대화가 {len(running)}개 있습니다. MCP 서버 구성은 "
            f"모든 라운드가 끝나거나 정지되어 최종 아티팩트가 나온 뒤에 바꿀 수 있습니다."
        )

    def _sync_mcp_lock(self) -> None:
        """진행 중인 토론 여부에 맞춰 잠금 상태를 갱신합니다 (바뀔 때만 다시 그림)."""
        if not self.alive:
            return
        reason = self._current_mcp_lock_reason()
        if bool(reason) == self.mcp_locked and reason == self.mcp_lock_reason:
            return
        self.mcp_locked = bool(reason)
        self.mcp_lock_reason = reason
        self._apply_mcp_lock()

    def refresh_mcp_lock(self) -> None:
        """토론이 시작·종료된 직후 화면에서 부릅니다 (타이머를 기다리지 않도록)."""
        self._sync_mcp_lock()

    def _apply_mcp_lock(self) -> None:
        if not self.alive:
            return
        if self.mcp_lock_badge and not self.mcp_lock_badge.is_deleted:
            self.mcp_lock_badge.set_visibility(self.mcp_locked)
        if self.mcp_lock_hint and not self.mcp_lock_hint.is_deleted:
            self.mcp_lock_hint.set_text(
                f"🔒 {self.mcp_lock_reason}" if self.mcp_locked else ""
            )
            self.mcp_lock_hint.set_visibility(self.mcp_locked)
        if self.mcp_add_btn and not self.mcp_add_btn.is_deleted:
            self.mcp_add_btn.disable() if self.mcp_locked else self.mcp_add_btn.enable()
        if self.reload_conf_btn and not self.reload_conf_btn.is_deleted:
            # 설정을 다시 읽으면 에이전트 풀이 통째로 바뀌고, MCP 서버까지 다시
            # 띄울 수 있습니다. 진행 중인 토론 밑에서 할 일이 아닙니다.
            self.reload_conf_btn.disable() if self.mcp_locked else self.reload_conf_btn.enable()
        self.refresh_mcp_status()
        # 카드의 도구 버튼도 같은 규칙으로 잠깁니다.
        self.refresh_agent_cards()

    def _blocked_by_running_debate(self) -> bool:
        """잠겨 있으면 알리고 True. 화면이 낡았을 때를 위한 마지막 확인입니다."""
        self._sync_mcp_lock()
        if self.mcp_locked:
            ui.notify(self.mcp_lock_reason, type="warning", position="bottom-right")
            return True
        return False

    # ------------------------------------------------------------------ MCP 구성 변경

    @staticmethod
    def _conf_path() -> str:
        """지금 앱이 읽고 있는 설정 파일. 없으면 기본값."""
        active = active_config_path()
        return str(active) if active is not None else "conf.toml"

    async def _apply_conf_change(
        self, write, done_message: str, restart_servers: bool = True
    ) -> None:
        """conf.toml 을 고치고, 바뀐 구성을 돌고 있는 앱에 그대로 반영합니다.

        파일만 고치고 끝내면 화면과 실제로 떠 있는 서버·에이전트가 어긋납니다.
        다음 기동 때까지 그 사실을 아무도 모르므로 여기서 바로 맞춥니다.

        `restart_servers` 는 서버 프로세스를 다시 띄울지입니다. 도구 할당처럼
        서버 구성이 그대로인 변경까지 전부 다시 띄우면 몇 초씩 걸리기만 하고
        얻는 것이 없습니다.
        """
        if self.mcp_add_btn and not self.mcp_add_btn.is_deleted:
            self.mcp_add_btn.disable()
        try:
            write()
        except Exception as e:  # noqa: BLE001 - 설정 오류는 화면에 그대로 알립니다
            logger.error(f"Could not update MCP servers in conf.toml: {e}", exc_info=True)
            ui.notify(f"conf.toml 을 고치지 못했습니다: {e}", type="negative", position="bottom-right")
            self.refresh_mcp_status()
            return
        finally:
            if self.mcp_add_btn and not self.mcp_add_btn.is_deleted and not self.mcp_locked:
                self.mcp_add_btn.enable()

        # `type="ongoing"` 은 타임아웃이 없어, 뒷일이 끝나기 전에 화면이 사라지면
        # 토스트가 영영 남습니다. 손잡이를 들고 있다가 직접 치우고, 그마저 못 하는
        # 경우를 대비해 제한 시간도 둡니다.
        progress = self._progress_toast("MCP 서버를 다시 띄우는 중입니다...") if restart_servers else None
        try:
            if restart_servers:
                await get_mcp_manager().reload_from_config()
            # 설정을 다시 읽었으므로 에이전트 풀도 그 구성으로 맞춥니다.
            reload_agent_pool()
            ui.notify(done_message, type="positive", position="bottom-right")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Applying the new configuration failed: {e}", exc_info=True)
            ui.notify(
                f"conf.toml 은 저장했지만 실행 중인 앱에 반영하지 못했습니다: {e}",
                type="negative", position="bottom-right",
            )
        finally:
            self._dismiss_toast(progress)
            self.refresh_mcp_status()
            self.refresh_agent_cards()
            self._refresh_workspace_hint()

    async def _on_mcp_toggle(self, server_name: str, enabled: bool) -> None:
        if self._blocked_by_running_debate():
            # 스위치를 사용자가 움직여 놓았으므로 실제 설정값으로 되돌립니다.
            self.refresh_mcp_status()
            return
        await self._apply_conf_change(
            lambda: set_mcp_server_enabled_in_conf_file(server_name, enabled, self._conf_path()),
            f"'{server_name}' 서버를 {'켰습니다' if enabled else '껐습니다'}.",
        )

    def _open_mcp_add_dialog(self) -> None:
        if self._blocked_by_running_debate():
            return

        with ui.dialog() as dialog, ui.card().classes(
            "p-4 w-[540px] max-w-full bg-slate-900 text-white border border-slate-700"
        ):
            ui.label("MCP 서버 추가").classes("text-lg font-bold")
            ui.label(
                "conf.toml 에 저장되어 지금 열려 있는 모든 대화와 앞으로 만드는 "
                "모든 대화에 함께 적용됩니다."
            ).classes("text-[11px] text-amber-400 mb-2 leading-snug")

            name_in = ui.input("서버 이름", placeholder="everything").props(
                "outlined dense dark"
            ).classes("w-full")
            command_in = ui.input(
                "실행 명령 (command)", placeholder="npx / node / ${PYTHON_BIN:-python}"
            ).props("outlined dense dark").classes("w-full")
            args_in = ui.textarea(
                "인자 (args) — 한 줄에 하나",
                placeholder="-y\n@modelcontextprotocol/server-everything",
            ).props("outlined dense dark rows=3").classes("w-full text-xs")
            env_in = ui.textarea(
                "환경변수 (env) — KEY=VALUE, 한 줄에 하나",
                placeholder="API_KEY=${SOME_API_KEY}",
            ).props("outlined dense dark rows=2").classes("w-full text-xs")
            enabled_cb = ui.checkbox("추가하고 바로 켜기", value=True).props("dense dark color=indigo-4")

            ui.label(
                "${VAR} 와 ${VAR:-기본값} 표기를 그대로 쓸 수 있습니다. 추가한 뒤에는 "
                "conf.toml 의 [agents.*].allowed_mcp_servers 에 이 이름을 넣어야 "
                "에이전트가 이 서버의 도구를 씁니다."
            ).classes("text-[10px] text-slate-500 leading-snug mt-1")

            async def do_add() -> None:
                name = (name_in.value or "").strip()
                command = (command_in.value or "").strip()
                if not name or not command:
                    ui.notify("서버 이름과 실행 명령은 반드시 입력해야 합니다.",
                              type="warning", position="bottom-right")
                    return

                args = [line.strip() for line in (args_in.value or "").splitlines() if line.strip()]
                env: Dict[str, str] = {}
                for line in (env_in.value or "").splitlines():
                    if not line.strip():
                        continue
                    if "=" not in line:
                        ui.notify(f"환경변수 형식이 올바르지 않습니다: '{line.strip()}' "
                                  f"(KEY=VALUE 로 적어주세요)", type="warning", position="bottom-right")
                        return
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()

                enabled = bool(enabled_cb.value)
                dialog.close()
                await self._apply_conf_change(
                    lambda: add_mcp_server_to_conf_file(
                        name, command, args, env, enabled, self._conf_path()
                    ),
                    f"'{name}' 서버를 추가했습니다." + ("" if enabled else " (꺼진 상태)"),
                )

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("취소", on_click=dialog.close).props("flat color=grey")
                ui.button("추가", on_click=do_add).props("unelevated color=indigo-6")

        dialog.open()

    def _open_mcp_delete_dialog(self, server_name: str) -> None:
        if self._blocked_by_running_debate():
            return

        # 이 서버를 쓰도록 지정된 에이전트가 있으면 미리 알립니다. 삭제해도
        # 토론은 돌지만, 그 에이전트는 도구 없이 발언하게 됩니다.
        users = [
            ag.name for ag in self.agent_pool.list_all()
            if server_name in (ag.allowed_mcp_servers or [])
        ]

        with ui.dialog() as dialog, ui.card().classes(
            "p-4 w-96 bg-slate-900 text-white border border-slate-700"
        ):
            ui.label("MCP 서버 삭제").classes("text-lg font-bold text-red-400 mb-1")
            ui.label(
                f"conf.toml 에서 [mcp_servers.{server_name}] 을 지우고 서버를 내립니다. "
                f"모든 대화에 함께 적용됩니다."
            ).classes("text-xs text-slate-300 leading-snug")
            if users:
                ui.label(
                    f"이 서버를 쓰도록 지정된 에이전트: {', '.join(users)}. "
                    f"삭제 후 이 에이전트들은 해당 도구 없이 토론합니다."
                ).classes("text-[11px] text-amber-400 mt-2 leading-snug")
            ui.label("서버 위에 적어 둔 설명 주석은 그대로 남습니다.").classes(
                "text-[10px] text-slate-500 mt-2"
            )

            async def do_delete() -> None:
                dialog.close()
                await self._apply_conf_change(
                    lambda: remove_mcp_server_from_conf_file(server_name, self._conf_path()),
                    f"'{server_name}' 서버를 삭제했습니다.",
                )

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("취소", on_click=dialog.close).props("flat color=grey")
                ui.button("삭제", on_click=do_delete).props("unelevated color=red-6")

        dialog.open()

    # ------------------------------------------------------------------ 설정 다시 읽기

    def _mcp_fingerprint(self) -> Dict[str, Any]:
        """지금 떠 있어야 할 MCP 서버 구성. 값이 달라지면 다시 띄워야 합니다."""
        try:
            servers = get_config().enabled_mcp_servers
        except Exception:  # noqa: BLE001
            return {}
        return {
            name: (cfg.command, tuple(cfg.args), tuple(sorted(cfg.env.items())))
            for name, cfg in servers.items()
        }

    def sync_agents_with_pool(self) -> None:
        """선택 상태를 지금 풀에 있는 에이전트에 맞춥니다.

        새로 생긴 에이전트는 켠 채로 둡니다 (기존 대화에서도 흐리게 보이지 않도록).
        설정에서 사라진 에이전트는 선택 목록에서도 지웁니다. 남겨 두면 그 키가
        대화의 `active_agents` 에 계속 저장되어, 있지도 않은 에이전트가 기록에
        쌓입니다.
        """
        live_keys = [agent.key for agent in self.agent_pool.list_all()]
        self.selected_agents = {
            key: self.selected_agents.get(key, True) for key in live_keys
        }

    async def _on_reload_conf(self) -> None:
        """conf.toml 을 다시 읽어 에이전트 풀(과 필요하면 MCP 서버)을 갱신합니다.

        앱을 다시 띄우지 않고 에이전트를 추가·수정하기 위한 버튼입니다. 설정
        파일이 깨져 있으면 아무것도 바꾸지 않고 돌아옵니다 — `get_config()` 는
        새 설정을 다 읽은 뒤에야 전역 값을 갈아 끼우므로, 실패하면 지금 돌고
        있는 구성이 그대로 남습니다.
        """
        if self._blocked_by_running_debate():
            return

        before_agents = {agent.key for agent in self.agent_pool.list_all()}
        before_mcp = self._mcp_fingerprint()

        progress = self._progress_toast("conf.toml 을 다시 읽는 중입니다...")
        try:
            try:
                get_config(reload=True, config_path=self._conf_path())
            except Exception as e:  # noqa: BLE001 - 깨진 설정으로 앱이 죽으면 안 됩니다
                logger.error(f"Could not reload conf.toml: {e}", exc_info=True)
                ui.notify(
                    f"conf.toml 을 읽지 못했습니다. 실행 중인 설정은 그대로입니다: {e}",
                    type="negative", position="bottom-right", multi_line=True,
                )
                return

            self.agent_pool = reload_agent_pool()
            self.sync_agents_with_pool()

            after_agents = {agent.key for agent in self.agent_pool.list_all()}
            added = sorted(after_agents - before_agents)
            removed = sorted(before_agents - after_agents)

            # MCP 구성까지 바뀌었다면 서버도 맞춰야 합니다. 그러지 않으면 화면은
            # 새 설정을, 도구는 옛 서버를 가리키게 됩니다.
            if self._mcp_fingerprint() != before_mcp:
                try:
                    await get_mcp_manager().reload_from_config()
                    ui.notify("MCP 서버 구성도 바뀌어 함께 다시 띄웠습니다.",
                              type="info", position="bottom-right")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"MCP reload after conf reload failed: {e}", exc_info=True)
                    ui.notify(f"에이전트는 갱신했지만 MCP 서버를 다시 띄우지 못했습니다: {e}",
                              type="negative", position="bottom-right")

            parts = [f"에이전트 {len(after_agents)}개"]
            if added:
                parts.append(f"추가: {', '.join(added)}")
            if removed:
                parts.append(f"제거: {', '.join(removed)}")
            if not added and not removed:
                parts.append("목록은 그대로 (모델·프롬프트 등은 갱신됨)")
            ui.notify("conf.toml 을 다시 읽었습니다 — " + " · ".join(parts),
                      type="positive", position="bottom-right", multi_line=True)
        finally:
            self._dismiss_toast(progress)
            self.refresh_agent_cards()
            self._update_summary_badge()
            self.refresh_mcp_status()
            self._refresh_workspace_hint()
            if self.on_config_changed:
                # 갱신된 로스터를 이 대화의 설정으로 저장합니다 (known_agents 포함).
                ui.timer(0.01, self.on_config_changed, once=True)

    def _open_agent_tools_dialog(self, agent_key: str) -> None:
        """이 에이전트가 호출할 수 있는 MCP 서버를 고릅니다.

        페르소나와 헷갈리기 쉽지만 다른 것입니다. 이름·역할·시스템 프롬프트는
        대화별로 갈리고 첫 발언과 함께 잠기지만, 도구 할당은 conf.toml 이 정본이라
        모든 대화에 함께 걸립니다. 그래서 잠금 규칙도 MCP 서버 쪽을 따릅니다.
        """
        if self._blocked_by_running_debate():
            return

        agent = self.agent_pool.get(agent_key)
        if agent is None:
            ui.notify(f"'{agent_key}' 에이전트를 찾을 수 없습니다.", type="warning", position="bottom-right")
            return

        try:
            configured = get_config().mcp_servers
            status = get_mcp_manager().connection_status()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not read MCP servers: {e}")
            configured, status = {}, {}

        allowed = list(agent.allowed_mcp_servers or [])
        # conf.toml 에서 이미 사라진 서버가 아직 적혀 있을 수 있습니다. 조용히
        # 지우지 않고 보여주고, 사용자가 체크를 풀어 정리하게 합니다.
        orphans = [name for name in allowed if name not in configured]
        boxes: Dict[str, ui.checkbox] = {}

        with ui.dialog() as dialog, ui.card().classes(
            "p-4 w-[520px] max-w-full bg-slate-900 text-white border border-slate-700"
        ):
            ui.label(f"{agent.name} · 도구 할당").classes("text-lg font-bold")
            ui.label(
                "이 에이전트가 발언 중 호출할 수 있는 MCP 서버입니다. conf.toml 에 저장되어 "
                "지금 열려 있는 모든 대화와 앞으로 만드는 모든 대화에 함께 적용됩니다."
            ).classes("text-[11px] text-amber-400 mb-2 leading-snug")

            if not configured and not orphans:
                ui.label("conf.toml 에 등록된 MCP 서버가 없습니다.").classes("text-xs text-slate-400")

            with ui.column().classes("w-full gap-1 max-h-[46vh] overflow-y-auto"):
                for name, server_cfg in configured.items():
                    info = status.get(name)
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        boxes[name] = ui.checkbox(value=name in allowed).props("dense dark color=indigo-4")
                        ui.label(name).classes("text-xs font-semibold text-slate-200")
                        if not server_cfg.enabled:
                            ui.badge("꺼짐", color="grey-8").props("dense text-[9px]")
                            ui.label("서버가 꺼져 있어 지금은 도구가 제공되지 않습니다").classes(
                                "text-[10px] text-slate-500 truncate"
                            )
                        elif info and info["connected"]:
                            ui.badge(f"툴 {info['tool_count']}", color="green-8").props("dense text-[9px]")
                        else:
                            ui.badge("연결 안 됨", color="red-9").props("dense text-[9px]")

                for name in orphans:
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        boxes[name] = ui.checkbox(value=True).props("dense dark color=amber-6")
                        ui.label(name).classes("text-xs font-semibold text-amber-300")
                        ui.badge("설정에 없음", color="amber-9").props("dense text-[9px]")

            st = agent.sequential_thinking
            if st.enabled and st.mode == "mcp":
                ui.label(
                    f"참고: 이 에이전트는 sequential_thinking 을 mcp 모드로 쓰므로 "
                    f"'{st.mcp_server}' 서버가 여기 선택과 무관하게 자동으로 포함됩니다."
                ).classes("text-[10px] text-slate-500 mt-2 leading-snug")

            async def do_save() -> None:
                servers = [name for name, box in boxes.items() if box.value]
                dialog.close()
                await self._apply_conf_change(
                    lambda: set_agent_allowed_mcp_servers_in_conf_file(
                        agent_key, servers, self._conf_path()
                    ),
                    f"'{agent.name}' 의 도구를 {len(servers)}개로 저장했습니다."
                    if servers else f"'{agent.name}' 에게 도구를 할당하지 않았습니다.",
                    # 서버 구성은 그대로입니다. 다시 띄울 이유가 없습니다.
                    restart_servers=False,
                )

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("취소", on_click=dialog.close).props("flat color=grey")
                ui.button("저장", on_click=do_save).props("unelevated color=indigo-6")

        dialog.open()

    def _progress_toast(self, message: str):
        """스스로 사라질 줄 아는 진행 토스트.

        예전에는 `type="ongoing"` 이라 제한 시간이 아예 없었습니다. 뒷일이 끝나기
        전에 화면이 갱신되거나 이벤트가 오지 않으면 토스트만 영영 남았습니다.

        이제는 제한 시간이 사라짐을 보장하고, 작업이 끝나면 `_dismiss_toast()` 가
        그보다 일찍 치웁니다. 사용자가 직접 닫을 수도 있습니다.

        토스트 엘리먼트는 **다시 그려지지 않는 슬롯**(로스터 루트)에 답니다. MCP 칩
        줄 안에서 만들면, 작업이 끝나고 그 줄을 다시 그릴 때 엘리먼트가 지워집니다.
        그러면 `dismiss()` 가 닿을 곳이 없어지고, 화면의 Quasar 토스트는 자기 포털에
        그대로 남습니다.
        """
        try:
            # `type="ongoing"` 은 쓰지 않습니다. Quasar 의 그 프리셋이 timeout 을 0 으로
            # 잡아 두어, 여기서 제한 시간을 줘도 무엇이 이기는지가 버전에 달립니다.
            # 스피너만 직접 켜면 모양은 같고 사라지는 것은 확실합니다.
            def _make():
                return ui.notification(
                    message, spinner=True, timeout=PROGRESS_TOAST_TIMEOUT,
                    close_button=True, position="bottom-right",
                )

            if self.expansion is not None and not self.expansion.is_deleted:
                with self.expansion:
                    return _make()
            return _make()
        except Exception as e:  # noqa: BLE001 - 알림을 못 띄운다고 작업을 막지 않습니다
            logger.debug(f"Could not show a progress toast: {e}")
            return None

    @staticmethod
    def _dismiss_toast(toast) -> None:
        if toast is None:
            return
        try:
            toast.dismiss()
        except Exception as e:  # noqa: BLE001 - 이미 사라진 토스트일 수 있습니다
            logger.debug(f"Could not dismiss a progress toast: {e}")

    async def _on_mcp_reconnect(self) -> None:
        """연결되지 않은 MCP 서버를 다시 띄웁니다."""
        if self.mcp_reconnect_btn:
            self.mcp_reconnect_btn.disable()
        progress = self._progress_toast("MCP 서버 재연결을 시도합니다...")
        try:
            changed = await get_mcp_manager().reconnect()
            self.refresh_mcp_status()
            if changed:
                ui.notify("MCP 서버에 다시 연결되었습니다.", type="positive", position="bottom-right")
            else:
                ui.notify("재연결할 서버가 없거나 여전히 실패했습니다. 서버 로그를 확인하세요.",
                          type="warning", position="bottom-right")
        except Exception as e:  # noqa: BLE001
            logger.error(f"MCP reconnect failed: {e}")
            ui.notify(f"재연결 중 오류: {e}", type="negative", position="bottom-right")
        finally:
            self._dismiss_toast(progress)
            if self.mcp_reconnect_btn:
                self.mcp_reconnect_btn.enable()

    def _refresh_workspace_hint(self) -> None:
        """입력값이 실제로 어느 폴더가 되는지 보여줍니다 (상대 경로·빈 값 포함)."""
        if self.workspace_hint is None or self.workspace_hint.is_deleted:
            return
        effective = resolve_workspace_dir(self.workspace_dir or None)
        live = get_mcp_manager().workspace
        text = f"현재 적용: {live}"
        if effective != live:
            text += f"   |   적용 대기: {effective}  ('적용' 을 누르세요)"
        self.workspace_hint.set_text(text)

    async def _on_workspace_apply(self) -> None:
        """작업 공간을 바꾸고 MCP 서버를 다시 띄웁니다.

        conf.toml 은 건드리지 않습니다. 이것은 대화의 설정입니다.
        """
        if self.workspace_input is None:
            return
        value = (self.workspace_input.value or "").strip()

        if self.session_id and get_debate_runner().is_running(self.session_id):
            ui.notify("토론이 진행 중입니다. 끝난 뒤에 바꾸세요.", type="warning", position="bottom-right")
            return
        others = get_debate_runner().running_elsewhere(self.session_id or "")
        if others:
            ui.notify(
                "다른 대화가 토론 중입니다. MCP 서버는 프로세스 전체가 공유하므로 "
                "지금 바꾸면 그 토론이 남의 폴더를 쓰게 됩니다.",
                type="warning", position="bottom-right",
            )
            return

        if self.workspace_apply_btn:
            self.workspace_apply_btn.disable()
        try:
            self.workspace_dir = value
            target = await get_mcp_manager().set_workspace(resolve_workspace_dir(value or None))
            self.refresh_mcp_status()
            self._refresh_workspace_hint()
            if self.on_config_changed:
                await self.on_config_changed()
            ui.notify(f"작업 공간을 '{target}' 로 바꾸고 MCP 서버를 다시 띄웠습니다.",
                      type="positive", position="bottom-right")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Workspace switch failed: {e}", exc_info=True)
            ui.notify(f"작업 공간 변경 실패: {e}", type="negative", position="bottom-right")
        finally:
            if self.workspace_apply_btn and not self.workspace_apply_btn.is_deleted:
                self.workspace_apply_btn.enable()

    def _on_agent_toggle(self, key: str, value: bool) -> None:
        self.selected_agents[key] = value
        self._update_summary_badge()
        if self.on_config_changed:
            ui.timer(0.01, self.on_config_changed, once=True)

    def _update_summary_badge(self) -> None:
        active_count = len([k for k, v in self.selected_agents.items() if v])
        if self.summary_badge:
            self.summary_badge.set_text(f"{active_count} Agents Active")

    def _on_strategy_change(self, e) -> None:
        self.strategy_name = e.value
        if self.on_config_changed:
            ui.timer(0.01, self.on_config_changed, once=True)

    def _on_rounds_change(self, e) -> None:
        self.max_rounds = int(e.value)
        if hasattr(self, "rounds_label") and self.rounds_label:
            self.rounds_label.set_text(str(self.max_rounds))
        if self.on_config_changed:
            ui.timer(0.01, self.on_config_changed, once=True)

    def _on_instructions_change(self, e) -> None:
        self.custom_instructions = e.value
        if self.on_config_changed:
            ui.timer(0.01, self.on_config_changed, once=True)

    @staticmethod
    def _is_selected(
        key: str, active_keys: List[str], known_keys: Optional[List[str]]
    ) -> bool:
        """이 대화에서 이 에이전트를 켜 둘지.

        `active_keys` 는 켜 둔 것만 담는 허용 목록이라, 목록에 없다는 사실만으로는
        "사용자가 끈 것" 과 "그 대화를 설정할 때는 없던 것" 을 구분할 수 없습니다.
        구분하지 않으면 둘 중 하나가 반드시 틀립니다 — conf.toml 에 에이전트를
        추가했더니 기존 대화에서 전부 꺼진 것으로 보이거나(지금까지의 증상),
        반대로 사용자가 끈 에이전트가 새로고침할 때마다 되살아납니다.

        `known_keys` 는 그 대화의 로스터를 저장할 때 존재하던 에이전트 전부입니다.
        여기에 없는 키는 그 뒤에 추가된 것이므로, 기본값(켜짐)으로 둡니다.
        """
        if not active_keys:
            # 로스터가 아예 비어 있는 대화 (예전 기록이나 손상된 행). 켤지 끌지를
            # 말해 주는 정보가 없으므로 기본값인 "전부 켜짐" 으로 둡니다.
            return True
        if key in active_keys:
            return True
        if known_keys:
            return key not in known_keys          # 저장 이후에 생긴 에이전트
        # 이 컬럼이 생기기 전에 만들어진 대화입니다. 그때 무엇이 있었는지 알 수 없어
        # 둘 중 하나는 틀리는데, 지금 있는 에이전트를 켜는 쪽을 고릅니다. 새 에이전트가
        # 모든 옛 대화에서 꺼져 보이는 것보다, 예전에 꺼 둔 에이전트가 한 번 되살아나고
        # 그 대화를 열어 보는 순간 기록이 남아 다시 정확해지는 편이 낫습니다.
        return True

    def known_agent_keys(self) -> List[str]:
        """지금 이 앱이 알고 있는 에이전트 전부 (선택 여부와 무관)."""
        return [agent.key for agent in self.agent_pool.list_all()]

    def get_active_agent_keys(self) -> List[str]:
        keys = [k for k, v in self.selected_agents.items() if v]
        if "orchestrator" not in keys:
            keys = ["orchestrator"] + keys
        return keys

    def load_from_session(
        self,
        active_keys: List[str],
        strategy: str,
        max_rounds: int,
        instructions: str,
        session_id: Optional[str] = None,
        workspace_dir: str = "",
        personas_locked: bool = False,
        personas: Optional[Dict[str, Any]] = None,
        known_keys: Optional[List[str]] = None,
    ) -> None:
        self.agent_pool = get_agent_pool()
        for k in self.agent_pool.list_all():
            self.selected_agents[k.key] = self._is_selected(k.key, active_keys, known_keys)
        self.strategy_name = strategy
        self.max_rounds = max_rounds
        self.custom_instructions = instructions
        self.workspace_dir = workspace_dir or ""
        self.session_id = session_id
        self.personas_locked = personas_locked
        if personas is not None:
            self.current_personas = personas

        self.refresh_agent_cards(self.current_personas)
        self._update_summary_badge()
        self._refresh_persona_controls()
        if self.strategy_select:
            self.strategy_select.value = strategy
        if self.rounds_slider:
            self.rounds_slider.value = max_rounds
        if hasattr(self, "rounds_label") and self.rounds_label:
            self.rounds_label.set_text(str(max_rounds))
        if self.custom_instr_input:
            self.custom_instr_input.value = instructions
        if self.workspace_input:
            self.workspace_input.value = self.workspace_dir
        self._refresh_workspace_hint()

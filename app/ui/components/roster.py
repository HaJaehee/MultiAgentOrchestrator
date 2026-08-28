from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional
from nicegui import ui
from app.agents.base import Agent
from app.agents.pool import AgentPool, get_agent_pool
from app.config import (
    active_config_path,
    add_mcp_server_to_conf_file,
    get_config,
    remove_mcp_server_from_conf_file,
    resolve_workspace_dir,
    set_mcp_server_enabled_in_conf_file,
)
from app.mcp.manager import get_mcp_manager
from app.orchestration.runner import get_debate_runner
from app.orchestration.strategies import STRATEGY_MAP

logger = logging.getLogger(__name__)


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
            "⚙️ 에이전트 풀 & 토론 설정 (Agent Roster & Settings)",
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
        self.refresh_mcp_status()

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

    async def _apply_mcp_change(self, write, done_message: str) -> None:
        """conf.toml 을 고치고, 그 구성으로 MCP 서버를 다시 띄웁니다.

        파일만 고치고 끝내면 화면과 실제로 떠 있는 서버가 어긋납니다. 다음 기동
        때까지 그 사실을 아무도 모르므로, 여기서 바로 반영합니다.
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

        ui.notify("MCP 서버를 다시 띄우는 중입니다...", type="ongoing", position="bottom-right")
        try:
            await get_mcp_manager().reload_from_config()
            ui.notify(done_message, type="positive", position="bottom-right")
        except Exception as e:  # noqa: BLE001
            logger.error(f"MCP reload failed: {e}", exc_info=True)
            ui.notify(
                f"conf.toml 은 저장했지만 서버를 다시 띄우지 못했습니다: {e}",
                type="negative", position="bottom-right",
            )
        finally:
            self.refresh_mcp_status()
            self._refresh_workspace_hint()

    async def _on_mcp_toggle(self, server_name: str, enabled: bool) -> None:
        if self._blocked_by_running_debate():
            # 스위치를 사용자가 움직여 놓았으므로 실제 설정값으로 되돌립니다.
            self.refresh_mcp_status()
            return
        await self._apply_mcp_change(
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
                await self._apply_mcp_change(
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
                await self._apply_mcp_change(
                    lambda: remove_mcp_server_from_conf_file(server_name, self._conf_path()),
                    f"'{server_name}' 서버를 삭제했습니다.",
                )

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("취소", on_click=dialog.close).props("flat color=grey")
                ui.button("삭제", on_click=do_delete).props("unelevated color=red-6")

        dialog.open()

    async def _on_mcp_reconnect(self) -> None:
        """연결되지 않은 MCP 서버를 다시 띄웁니다."""
        if self.mcp_reconnect_btn:
            self.mcp_reconnect_btn.disable()
        try:
            ui.notify("MCP 서버 재연결을 시도합니다...", type="ongoing", position="bottom-right")
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
    ) -> None:
        self.agent_pool = get_agent_pool()
        for k in self.agent_pool.list_all():
            self.selected_agents[k.key] = (k.key in active_keys) if active_keys else True
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

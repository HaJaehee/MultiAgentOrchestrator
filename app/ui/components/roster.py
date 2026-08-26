import logging
from typing import Callable, Coroutine, Dict, List, Optional
from nicegui import ui
from app.agents.base import Agent
from app.agents.pool import AgentPool, get_agent_pool
from app.config import get_config
from app.mcp.manager import get_mcp_manager
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

        # Init selection defaults
        for ag in self.agent_pool.list_all():
            self.selected_agents[ag.key] = True

        self.strategy_select: Optional[ui.select] = None
        self.rounds_slider: Optional[ui.slider] = None
        self.custom_instr_input: Optional[ui.textarea] = None
        self.expansion: Optional[ui.expansion] = None
        self.summary_badge: Optional[ui.badge] = None
        self.mcp_row: Optional[ui.row] = None
        self.mcp_badge: Optional[ui.badge] = None
        self.mcp_reconnect_btn: Optional[ui.button] = None

    def build_ui(self) -> ui.expansion:
        # Expansion defaulted to open as requested
        self.expansion = ui.expansion(
            "⚙️ 에이전트 풀 & 토론 설정 (Agent Roster & Settings)",
            icon="tune",
            value=True,  # Expanded by default
        ).classes("w-full bg-slate-900 border border-slate-800 rounded-xl shadow-md text-slate-100 flex-shrink-0")

        with self.expansion:
            with ui.column().classes("w-full p-2 gap-3"):
                # 1. Agent Selection Cards
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("토론 참여 에이전트 선택").classes("text-xs font-bold text-slate-300")
                    self.summary_badge = ui.badge("4 Agents Active", color="indigo-7").props("dense text-xs")

                with ui.row().classes("w-full gap-2 flex-wrap"):
                    for ag in self.agent_pool.list_all():
                        self._build_agent_card(ag)

                ui.separator().classes("bg-slate-800 my-1")

                # 2. MCP Tool Servers status
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("MCP 도구 서버").classes("text-xs font-bold text-slate-300")
                        self.mcp_badge = ui.badge("-", color="slate-7").props("dense text-xs")
                    self.mcp_reconnect_btn = (
                        ui.button(icon="refresh", on_click=self._on_mcp_reconnect)
                        .props("flat dense round size=sm color=slate-4")
                    )
                    self.mcp_reconnect_btn.tooltip("연결되지 않은 MCP 서버 다시 시도")

                self.mcp_row = ui.row().classes("w-full gap-2 flex-wrap items-center")
                self.refresh_mcp_status()

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

    def _build_agent_card(self, agent: Agent) -> None:
        is_orchestrator = (agent.key == "orchestrator")
        is_active = self.selected_agents.get(agent.key, True)

        card_cls = "p-2 rounded-lg border flex-grow max-w-[240px] min-w-[180px] transition-all "
        card_cls += "bg-slate-800/90 border-indigo-500/60" if is_active else "bg-slate-900/60 border-slate-800 opacity-50"

        with ui.card().classes(card_cls):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                with ui.row().classes("items-center gap-2 min-w-0"):
                    ui.avatar(agent.avatar, color=agent.color, text_color="white", size="xs")
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(agent.name).classes("text-xs font-bold truncate")
                        ui.label(agent.role).classes("text-[9px] text-slate-400 truncate")

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
                    ui.badge("SIM", color="grey-8").props("dense text-[8px]")

            ui.tooltip(
                f"model: {agent.model}\n"
                f"endpoint: {agent.endpoint_label}\n"
                f"temperature: {agent.temperature} / max_tokens: {agent.max_tokens}\n"
                f"sequential thinking: "
                f"{f'{agent.sequential_thinking.mode} (max {agent.sequential_thinking.max_steps} steps)' if agent.sequential_thinking.enabled else 'disabled'}\n"
                f"mcp: {', '.join(agent.allowed_mcp_servers) or '-'}"
            ).classes("whitespace-pre-line text-[10px]")

    def refresh_mcp_status(self) -> None:
        """conf.toml 의 MCP 서버별 연결 상태를 칩으로 다시 그립니다."""
        if self.mcp_row is None:
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
                    "flex items-center gap-1 px-2 py-1 rounded-md bg-slate-800/70 border border-slate-700"
                ):
                    ui.icon(icon, size="12px").classes(icon_cls)
                    ui.label(name).classes("text-[10px] font-semibold text-slate-200")
                    ui.badge(detail, color=color).props("dense")
                    ui.tooltip(tip).classes("whitespace-pre-line text-[10px]")

        enabled_total = len([c for c in configured.values() if c.enabled])
        if self.mcp_badge:
            summary_color = (
                "green-7" if enabled_total and connected_count == enabled_total
                else "amber-8" if connected_count
                else "red-8"
            )
            self.mcp_badge.set_text(f"{connected_count}/{enabled_total} 연결됨")
            self.mcp_badge.props(f"dense color={summary_color}")

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

    def load_from_session(self, active_keys: List[str], strategy: str, max_rounds: int, instructions: str) -> None:
        for k in self.agent_pool.list_all():
            self.selected_agents[k.key] = (k.key in active_keys) if active_keys else True
        self.strategy_name = strategy
        self.max_rounds = max_rounds
        self.custom_instructions = instructions

        self._update_summary_badge()
        if self.strategy_select:
            self.strategy_select.value = strategy
        if self.rounds_slider:
            self.rounds_slider.value = max_rounds
        if hasattr(self, "rounds_label") and self.rounds_label:
            self.rounds_label.set_text(str(max_rounds))
        if self.custom_instr_input:
            self.custom_instr_input.value = instructions

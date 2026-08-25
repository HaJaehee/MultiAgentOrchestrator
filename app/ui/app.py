import asyncio
import logging
import uuid
from typing import Any, Dict, Optional
from nicegui import app, ui
from sqlalchemy import desc, select
from app.database.models import ArtifactModel, MessageModel, SessionModel
from app.database.session import get_session_factory
from app.orchestration.engine import get_orchestrator_engine
from app.ui.components.artifact_viewer import ArtifactViewer
from app.ui.components.chat_feed import ChatFeed
from app.ui.components.roster import AgentRosterControl
from app.ui.components.sidebar import SessionSidebar
from app.ui.theme import CUSTOM_CSS, FAVICON_SVG

logger = logging.getLogger(__name__)


def create_ui() -> None:
    """Configures the main NiceGUI web application page and event bindings."""

    @ui.page("/", title="Multi-Agent Orchestrator", favicon=FAVICON_SVG)
    async def index_page():
        ui.dark_mode(True)
        ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")

        session_factory = get_session_factory()
        engine = get_orchestrator_engine()

        current_session_id: Optional[str] = None

        # 1. Instantiate Component Controllers
        async def on_session_selected(sid: str) -> None:
            nonlocal current_session_id
            current_session_id = sid
            await load_session_state(sid)

        async def on_new_session() -> None:
            nonlocal current_session_id
            sid = await create_new_session_db()
            current_session_id = sid
            sidebar.current_session_id = sid
            await load_session_state(sid)

        async def on_config_changed() -> None:
            nonlocal current_session_id
            if not current_session_id:
                return
            async with session_factory() as db:
                stmt = select(SessionModel).where(SessionModel.id == current_session_id)
                res = await db.execute(stmt)
                curr = res.scalar_one_or_none()
                if curr:
                    curr.active_agents = roster_control.get_active_agent_keys()
                    curr.strategy = roster_control.strategy_name
                    curr.max_rounds = roster_control.max_rounds
                    curr.custom_instructions = roster_control.custom_instructions
                    await db.commit()

        async def on_send_message(prompt: str) -> None:
            nonlocal current_session_id
            if not current_session_id:
                current_session_id = await create_new_session_db(title=prompt[:30])
                sidebar.current_session_id = current_session_id

            # Save latest config before running
            await on_config_changed()

            async def handle_engine_event(event: Dict[str, Any]) -> None:
                etype = event.get("type")
                if etype == "status_changed":
                    status = event.get("status", "")
                    speaker = event.get("speaker", "")
                    round_num = event.get("round", "")
                    label = f"[{speaker}] 발언 및 분석 중..." if speaker else f"상태: {status}"
                    round_info = f"Round {round_num}" if round_num else "Debating"
                    chat_feed.set_busy(True, label, round_info)
                elif etype == "round_started":
                    r = event.get("round", 1)
                    mr = event.get("max_rounds", 3)
                    chat_feed.set_busy(True, f"Round {r}/{mr} 전문가 토론 진행 중...", f"Round {r}/{mr}")
                elif etype == "message_added":
                    chat_feed.append_message(event.get("message", {}))
                elif etype == "artifacts_synthesized":
                    artifact_viewer.render_artifacts(event.get("artifacts", []))
                elif etype == "turn_completed":
                    chat_feed.set_busy(False, "토론 완료 및 최종 아티팩트 합성 완료", "Done")
                    await sidebar.refresh_list()

            try:
                await engine.run_turn(
                    session_id=current_session_id,
                    user_prompt=prompt,
                    on_event=handle_engine_event,
                )
            except Exception as e:
                logger.error(f"Error during orchestrator turn: {e}", exc_info=True)
                chat_feed.set_busy(False, f"오류 발생: {str(e)}", "Error")
                ui.notify(f"토론 실행 중 오류가 발생했습니다: {str(e)}", type="negative")

        # 2. Build Layout Containers
        sidebar = SessionSidebar(on_session_selected, on_new_session)
        drawer = sidebar.build_ui()

        roster_control = AgentRosterControl(on_config_changed)
        chat_feed = ChatFeed(on_send_message)
        artifact_viewer = ArtifactViewer()

        # Top Header
        with ui.header().classes("bg-slate-900 border-b border-slate-800 px-4 py-2 items-center justify-between"):
            with ui.row().classes("items-center gap-2.5"):
                ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round color=grey-4").tooltip("사이드바 열기/닫기")
                ui.icon("forum", size="sm").classes("text-indigo-400 mr-0.5")
                with ui.column().classes("gap-0"):
                    ui.label("Multi-Agent Orchestrator Platform").classes("text-base font-bold text-white tracking-wide")
                    ui.label("MCP-enabled Autonomous Collaborative Debate & Synthesis").classes("text-[11px] text-slate-400")

            with ui.row().classes("items-center gap-2"):
                ui.badge("FastAPI + NiceGUI", color="indigo-8").props("dense")
                ui.badge("MCP Host", color="teal-8").props("dense")
                ui.badge("LiteLLM Multi-Model", color="purple-8").props("dense")

        # Main Splitter Workspace (58% Debate Feed / 42% Artifact Viewer)
        with ui.splitter(value=58).classes("w-full h-[calc(100vh-65px)] overflow-hidden") as splitter:
            with splitter.before:
                with ui.column().classes("w-full h-full p-3 gap-3 overflow-hidden flex flex-col"):
                    roster_control.build_ui()
                    with ui.card().classes("w-full flex-grow bg-slate-900/70 border border-slate-800 p-3 rounded-xl shadow-lg flex flex-col min-h-0 overflow-hidden"):
                        chat_feed.build_ui()

            with splitter.after:
                with ui.column().classes("w-full h-full p-3 overflow-hidden flex flex-col"):
                    artifact_viewer.build_ui()

        # Helper DB Functions
        async def create_new_session_db(title: str = "New Collaborative Debate") -> str:
            sid = str(uuid.uuid4())
            async with session_factory() as db:
                new_session = SessionModel(
                    id=sid,
                    title=title,
                    strategy=roster_control.strategy_name,
                    max_rounds=roster_control.max_rounds,
                    active_agents=roster_control.get_active_agent_keys(),
                    custom_instructions=roster_control.custom_instructions,
                )
                db.add(new_session)
                await db.commit()
            return sid

        async def load_session_state(sid: str) -> None:
            chat_feed.clear()
            artifact_viewer.render_artifacts([])

            async with session_factory() as db:
                # Load session settings
                stmt_s = select(SessionModel).where(SessionModel.id == sid)
                res_s = await db.execute(stmt_s)
                s_obj = res_s.scalar_one_or_none()
                if s_obj:
                    roster_control.load_from_session(
                        active_keys=s_obj.active_agents or [],
                        strategy=s_obj.strategy or "free_debate",
                        max_rounds=s_obj.max_rounds or 3,
                        instructions=s_obj.custom_instructions or "",
                    )

                # Load messages
                stmt_m = select(MessageModel).where(MessageModel.session_id == sid).order_by(MessageModel.created_at)
                res_m = await db.execute(stmt_m)
                messages_db = res_m.scalars().all()

                formatted_msgs = []
                for m in messages_db:
                    formatted_msgs.append({
                        "id": m.id,
                        "sender_key": m.sender_key,
                        "sender_name": m.sender_name,
                        "sender_role": m.sender_role,
                        "content": m.content,
                        "round_number": m.round_number,
                        "msg_type": m.msg_type,
                        "tool_calls": [
                            {
                                "tool_name": tc.tool_name,
                                "arguments": tc.arguments,
                                "output": tc.output,
                                "status": tc.status,
                            }
                            for tc in m.tool_calls
                        ] if hasattr(m, "tool_calls") and m.tool_calls else [],
                    })

                if formatted_msgs:
                    chat_feed.render_all(formatted_msgs)

                # Load artifacts
                stmt_a = select(ArtifactModel).where(ArtifactModel.session_id == sid).order_by(ArtifactModel.created_at)
                res_a = await db.execute(stmt_a)
                arts_db = res_a.scalars().all()

                formatted_arts = [
                    {
                        "id": a.id,
                        "artifact_type": a.artifact_type,
                        "title": a.title,
                        "content": a.content,
                        "language": a.language,
                    }
                    for a in arts_db
                ]
                if formatted_arts:
                    artifact_viewer.render_artifacts(formatted_arts)

        # Initialize on initial load: select most recent session or create new
        async with session_factory() as db:
            stmt = select(SessionModel).order_by(desc(SessionModel.updated_at)).limit(1)
            res = await db.execute(stmt)
            latest_session = res.scalar_one_or_none()

        if latest_session:
            current_session_id = latest_session.id
            sidebar.current_session_id = latest_session.id
            await load_session_state(latest_session.id)
        else:
            await on_new_session()

        await sidebar.refresh_list()

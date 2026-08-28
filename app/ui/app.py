import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Set
from nicegui import ui
from sqlalchemy import desc, select
from app.database.models import ArtifactModel, MessageModel, SessionModel
from app.database.session import get_session_factory
from app.orchestration.runner import TurnRun, WorkspaceConflictError, get_debate_runner
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
        runner = get_debate_runner()
        client = ui.context.client

        current_session_id: Optional[str] = None
        # 이 페이지가 붙어 있는 백그라운드 토론의 구독. 페이지가 사라지면 구독만
        # 끊고 토론은 계속 굴러갑니다.
        consumer_task: Optional[asyncio.Task] = None
        subscription: Optional["asyncio.Queue[Dict[str, Any]]"] = None
        subscribed_run: Optional[TurnRun] = None

        # ------------------------------------------------------------ 구독

        def detach_from_run() -> None:
            nonlocal consumer_task, subscription, subscribed_run
            if subscribed_run is not None and subscription is not None:
                subscribed_run.unsubscribe(subscription)
            if consumer_task is not None and not consumer_task.done():
                consumer_task.cancel()
            consumer_task = None
            subscription = None
            subscribed_run = None

        async def apply_event(event: Dict[str, Any]) -> None:
            """백그라운드 토론 이벤트를 이 화면에 반영합니다."""
            etype = event.get("type")

            if etype == "status_changed":
                speaker = event.get("speaker", "")
                status = event.get("status", "")
                round_num = event.get("round", "")
                label = f"[{speaker}] 발언 및 분석 중..." if speaker else f"상태: {status}"
                round_info = f"Round {round_num}" if round_num else "Debating"
                chat_feed.set_busy(True, label, round_info)
            elif etype == "round_started":
                r = event.get("round", 1)
                mr = event.get("max_rounds", 3)
                chat_feed.set_busy(True, f"Round {r}/{mr} 전문가 토론 진행 중...", f"Round {r}/{mr}")
            elif etype == "message_stream_start":
                chat_feed.start_streaming_message(event.get("message", {}))
            elif etype == "message_stream_chunk":
                chat_feed.append_stream_chunk(event.get("message_id", ""), event.get("delta", ""))
            elif etype == "message_added":
                chat_feed.append_message(event.get("message", {}))
            elif etype == "artifacts_synthesized":
                artifact_viewer.render_artifacts(event.get("artifacts", []))
            elif etype == "turn_completed":
                failed = event.get("failed_agents") or []
                if failed:
                    chat_feed.set_busy(False, f"토론 완료 — 응답하지 못한 에이전트: {', '.join(failed)}", "Incomplete")
                    ui.notify(
                        f"일부 에이전트가 LLM 엔드포인트에 연결하지 못했습니다: {', '.join(failed)}",
                        type="warning",
                        position="bottom-right",
                    )
                else:
                    chat_feed.set_busy(False, "토론 완료 및 최종 아티팩트 합성 완료", "Done")
                # 첫 턴에서 페르소나가 고정되었으므로 편집 버튼을 잠금 상태로 바꿉니다.
                roster_control.set_personas_locked(True)
                await sidebar.refresh_list()
            elif etype == "run_finished":
                status = event.get("status")
                if status == "failed":
                    error = event.get("error") or "알 수 없는 오류"
                    chat_feed.set_busy(False, f"오류로 중단됨: {error}", "Error")
                    ui.notify(f"토론 실행 중 오류가 발생했습니다: {error}", type="negative")
                elif status == "cancelled":
                    chat_feed.set_busy(False, "토론이 취소되었습니다.", "Cancelled")
                else:
                    chat_feed.set_busy(False)

        async def consume(run: TurnRun, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
            """구독 큐를 화면에 흘려보냅니다.

            이 태스크는 페이지의 것입니다. 페이지가 사라지면 여기서 멈추고,
            토론 태스크는 그대로 살아 있습니다.
            """
            nonlocal consumer_task, subscription, subscribed_run
            try:
                while True:
                    event = await queue.get()
                    if client.is_deleted or not chat_feed.alive:
                        break
                    try:
                        # 이벤트 처리 중 ui.notify 등이 클라이언트 컨텍스트를 필요로 합니다.
                        with client:
                            await apply_event(event)
                    except RuntimeError as exc:
                        # 갱신 도중 페이지가 사라진 경우. 토론은 계속됩니다.
                        logger.debug(f"Stopped feeding a closed page: {exc}")
                        break
                    if event.get("type") == "run_finished":
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Debate event consumer stopped: {exc}", exc_info=True)
            finally:
                run.unsubscribe(queue)
                if subscription is queue:
                    consumer_task = None
                    subscription = None
                    subscribed_run = None

        def attach_to_run(run: TurnRun) -> None:
            """진행 중인 토론에 이 화면을 붙입니다 (새로고침 후 재접속 포함)."""
            nonlocal consumer_task, subscription, subscribed_run
            detach_from_run()
            if run.status != "running":
                return
            subscribed_run = run
            subscription = run.subscribe()
            consumer_task = asyncio.create_task(consume(run, subscription))

        client.on_delete(lambda: detach_from_run())

        # ------------------------------------------------------------ 콜백

        async def on_session_selected(sid: str) -> None:
            nonlocal current_session_id
            current_session_id = sid
            await load_session_state(sid)

        async def on_new_session() -> None:
            nonlocal current_session_id
            sid = await create_new_session_db()
            current_session_id = sid
            sidebar.current_session_id = sid
            roster_control.set_personas_locked(False)
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
                    curr.workspace_dir = roster_control.workspace_dir
                    await db.commit()

        async def on_send_message(prompt: str) -> None:
            """토론을 시작하고 이 화면을 붙입니다. 실제 실행은 러너가 맡습니다.

            `ChatFeed._handle_send()` 가 이미 입력을 잠근 채로 들어오므로, 시작하지
            못하고 돌아갈 때는 반드시 다시 풀어 주어야 합니다.
            """
            nonlocal current_session_id
            try:
                if not current_session_id:
                    current_session_id = await create_new_session_db(title=prompt[:30])
                    sidebar.current_session_id = current_session_id
                    roster_control.session_id = current_session_id

                if runner.is_running(current_session_id):
                    ui.notify("이 세션의 토론이 아직 진행 중입니다.", type="warning", position="bottom-right")
                    chat_feed.set_busy(False, "이미 진행 중인 토론이 있습니다.", "Running")
                    return

                # Save latest config before running
                await on_config_changed()

                # 토론은 이 페이지가 아니라 프로세스가 소유합니다. 새로고침하거나
                # 페르소나 화면에 다녀와도 중단되지 않고, 돌아오면 다시 이어 붙습니다.
                run = runner.start(current_session_id, prompt, roster_control.workspace_dir)
            except WorkspaceConflictError as exc:
                chat_feed.set_busy(False, str(exc), "Blocked")
                ui.notify(str(exc), type="warning", position="bottom-right")
                return
            except Exception as exc:  # noqa: BLE001 - 입력이 잠긴 채로 남으면 안 됩니다
                logger.error(f"Could not start the debate: {exc}", exc_info=True)
                chat_feed.set_busy(False, f"토론을 시작하지 못했습니다: {exc}", "Error")
                ui.notify(f"토론을 시작하지 못했습니다: {exc}", type="negative")
                return

            chat_feed.set_busy(True, run.status_text, run.round_info)
            attach_to_run(run)
            if run.status != "running":
                chat_feed.set_busy(False, run.status_text, run.round_info)

        # ------------------------------------------------------------ 레이아웃

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
                # flex-nowrap 이 없으면 NiceGUI 컬럼 기본값(flex-wrap: wrap) 때문에
                # 로스터가 커졌을 때 채팅 카드가 다음 열로 줄바꿈되어 화면 밖으로 밀려납니다.
                with ui.column().classes(
                    "w-full h-full p-3 gap-3 overflow-hidden flex flex-col flex-nowrap"
                ):
                    roster_control.build_ui()
                    with ui.card().classes(
                        "w-full flex-grow bg-slate-900/70 border border-slate-800 p-3 rounded-xl "
                        "shadow-lg flex flex-col flex-nowrap min-h-[240px] overflow-hidden"
                    ):
                        chat_feed.build_ui()

            with splitter.after:
                with ui.column().classes(
                    "w-full h-full p-3 overflow-hidden flex flex-col flex-nowrap"
                ):
                    artifact_viewer.build_ui()

        # ------------------------------------------------------------ DB 헬퍼

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
                    workspace_dir=roster_control.workspace_dir,
                )
                db.add(new_session)
                await db.commit()
            return sid

        async def load_session_state(sid: str) -> None:
            """세션 화면을 DB 기록과 진행 중인 토론 스냅샷으로 다시 구성합니다."""
            detach_from_run()
            chat_feed.clear()
            artifact_viewer.render_artifacts([])

            async with session_factory() as db:
                # Load session settings
                stmt_s = select(SessionModel).where(SessionModel.id == sid)
                res_s = await db.execute(stmt_s)
                s_obj = res_s.scalar_one_or_none()
                if s_obj:
                    from app.agents.personas import effective_personas
                    personas = await effective_personas(db, sid, roster_control.agent_pool)
                    roster_control.load_from_session(
                        active_keys=s_obj.active_agents or [],
                        strategy=s_obj.strategy or "free_debate",
                        max_rounds=s_obj.max_rounds or 3,
                        instructions=s_obj.custom_instructions or "",
                        session_id=sid,
                        personas_locked=bool(s_obj.personas_locked),
                        personas=personas,
                        workspace_dir=s_obj.workspace_dir or "",
                    )

                # Load messages
                stmt_m = select(MessageModel).where(MessageModel.session_id == sid).order_by(MessageModel.created_at)
                res_m = await db.execute(stmt_m)
                messages_db = res_m.scalars().all()

                formatted_msgs: List[Dict[str, Any]] = []
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

            # 진행 중인 토론이 있으면, 아직 DB 에 커밋되지 않은 발언까지 얹습니다.
            # 끝난 토론은 전부 DB 에 들어가 있으므로 스냅샷을 보지 않습니다.
            # (스냅샷을 계속 신뢰하면 지난 턴의 산출물이 DB 기록을 덮어씁니다.)
            run = runner.get(sid)
            running = run is not None and run.status == "running"
            streaming_ids: Set[str] = set()
            if running:
                snapshot = run.snapshot()
                known = {m["id"] for m in formatted_msgs if m.get("id")}
                formatted_msgs.extend(m for m in snapshot["messages"] if m.get("id") not in known)
                streaming_ids = snapshot["streaming_ids"]
                if snapshot["artifacts"]:
                    formatted_arts = snapshot["artifacts"]

            if formatted_msgs:
                chat_feed.render_all(formatted_msgs, streaming_ids=streaming_ids)
            if formatted_arts:
                artifact_viewer.render_artifacts(formatted_arts)

            if running:
                chat_feed.set_busy(run.busy, run.status_text, run.round_info)
                attach_to_run(run)
            else:
                chat_feed.set_busy(False, "대기 중 (Ready for prompt)", "Ready")

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

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Set
from nicegui import ui
from sqlalchemy import desc, select
from app.about import (
    ABOUT_LINE,
    APP_NAME,
    APP_SHORT_NAME,
    APP_TAGLINE,
    APP_VERSION_LABEL,
    AUTHOR,
    AUTHOR_EMAIL,
    LICENSE_NAME,
)
from app.agents.personas import (
    effective_personas,
    resync_agent_configs,
    session_roster_agents,
)
from app.database.models import ArtifactModel, MessageModel, SessionModel
from app.database.session import get_session_factory
from app.orchestration.runner import TurnRun, WorkspaceConflictError, get_debate_runner
from app.orchestration.strategies import resolve_strategy_name
from app.ui.components.artifact_viewer import ArtifactViewer
from app.ui.components.chat_feed import ChatFeed, clip_tool_output
from app.ui.components.roster import AgentRosterControl
from app.ui.components.sidebar import SessionSidebar
from app.ui.clipboard import copy_to_clipboard
from app.ui.theme import CUSTOM_CSS, FAVICON_SVG

logger = logging.getLogger(__name__)


def create_ui() -> None:
    """Configures the main NiceGUI web application page and event bindings."""

    @ui.page("/", title="MADO: Multi-Agent Debate & Orchestration Platform", favicon=FAVICON_SVG)
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
            elif etype == "stop_requested":
                # 정지를 누른 화면뿐 아니라 같은 세션을 보고 있는 모든 화면이
                # 같은 상태를 보아야 합니다.
                chat_feed.set_busy(
                    True,
                    "정지 요청됨 — 진행 중인 발언을 마친 뒤 지금까지의 토론으로 합성합니다.",
                    "Stopping",
                )
                chat_feed.set_stop_pending(True)
                ui.notify(
                    "정지를 요청했습니다. 진행 중인 발언을 마치는 대로 합성으로 넘어갑니다.",
                    type="warning",
                    position="bottom-right",
                )
            elif etype == "interjection_queued":
                pending = event.get("pending", 0)
                if event.get("deferred"):
                    # 합성이 시작된 뒤에는 이번 턴에 실을 자리가 없습니다.
                    # "다음 발언 차례에 반영" 이라고 알려 놓고 조용히 버리면 안 됩니다.
                    ui.notify(
                        "최종 합성이 이미 시작되어 이번 턴에는 반영되지 않습니다. "
                        "기록에는 남고 다음 요청부터 반영됩니다.",
                        type="warning",
                        position="bottom-right",
                    )
                else:
                    ui.notify(
                        f"개입 메시지를 전달했습니다 (대기 {pending}건). 다음 발언 차례에 반영됩니다.",
                        type="info",
                        position="bottom-right",
                    )
            elif etype == "interjections_deferred":
                count = event.get("count", 0)
                ui.notify(
                    f"개입 {count}건은 합성 이후에 도착해 이번 턴에 반영되지 못했습니다. "
                    f"기록에 남겨 두었으니 다음 요청에서 이어집니다.",
                    type="warning",
                    position="bottom-right",
                )
            elif etype == "turn_completed":
                failed = event.get("failed_agents") or []
                if failed:
                    chat_feed.set_busy(False, f"토론 완료 — 응답하지 못한 에이전트: {', '.join(failed)}", "Incomplete")
                    ui.notify(
                        f"일부 에이전트가 LLM 엔드포인트에 연결하지 못했습니다: {', '.join(failed)}",
                        type="warning",
                        position="bottom-right",
                    )
                elif event.get("stopped_early"):
                    rounds = event.get("rounds_completed", 0)
                    max_rounds = event.get("max_rounds", 0)
                    chat_feed.set_busy(
                        False,
                        f"사용자 요청으로 정지 — {rounds}/{max_rounds} 라운드까지의 토론으로 합성했습니다.",
                        "Stopped",
                    )
                    ui.notify(
                        "정지 요청대로 지금까지의 토론만으로 최종 산출물을 만들었습니다.",
                        type="info",
                        position="bottom-right",
                    )
                else:
                    chat_feed.set_busy(False, "토론 완료 및 최종 아티팩트 합성 완료", "Done")
                # 첫 턴에서 페르소나가 고정되었으므로 편집 버튼을 잠금 상태로 바꿉니다.
                roster_control.set_personas_locked(True)
                await sidebar.refresh_list()
            elif etype == "run_finished":
                # 토론이 끝났으므로 MCP 서버 구성을 다시 만질 수 있습니다. 상태가
                # 확정되는 시점이 여기라, 잠금 해제도 여기서 알립니다.
                roster_control.refresh_mcp_lock()
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
                    # 지금 무엇이 있었는지 함께 남깁니다. 나중에 에이전트가 추가되면
                    # 이 목록과 비교해 "새로 생긴 것" 만 켜진 채로 보여줍니다.
                    curr.known_agents = roster_control.known_agent_keys()
                    curr.strategy = roster_control.strategy_name
                    curr.max_rounds = roster_control.max_rounds
                    curr.parallel_limit = roster_control.parallel_limit
                    curr.custom_instructions = roster_control.custom_instructions
                    curr.workspace_dir = roster_control.workspace_dir
                    await db.commit()

        async def on_resync_agents() -> None:
            """잠긴 대화가 굳혀 둔 에이전트 구성을 지금 conf.json 값으로 다시 맞춥니다.

            대화가 자기완결적이 된 대가입니다. 엔드포인트나 API 키가 바뀌면 옛 대화가
            죽은 주소를 계속 두드리므로, 인격은 그대로 두고 운영 설정만 다시 굳힙니다.
            """
            if not current_session_id:
                return
            pool = roster_control.agent_pool
            async with session_factory() as db:
                res = await db.execute(
                    select(SessionModel).where(SessionModel.id == current_session_id)
                )
                s_obj = res.scalar_one_or_none()
                if s_obj is None:
                    return
                updated = await resync_agent_configs(db, s_obj, pool)
                agents = await session_roster_agents(db, s_obj, pool)
                personas = await effective_personas(db, current_session_id, pool)

            roster_control.set_session_agents(agents)
            roster_control.refresh_agent_cards(personas)
            if updated:
                ui.notify(
                    f"에이전트 {len(updated)}개의 모델·엔드포인트·도구를 conf.json 값으로 "
                    f"갱신했습니다 ({', '.join(sorted(updated))}). 페르소나는 그대로입니다.",
                    type="positive", position="bottom-right", multi_line=True,
                )
            else:
                ui.notify(
                    "갱신할 에이전트가 없습니다. 이 대화의 에이전트가 모두 conf.json 에서 "
                    "사라졌습니다.",
                    type="warning", position="bottom-right", multi_line=True,
                )

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
            # 이 대화가 도구를 쓰기 시작했습니다. MCP 구성은 여기서부터 잠깁니다.
            roster_control.refresh_mcp_lock()
            attach_to_run(run)
            if run.status != "running":
                chat_feed.set_busy(False, run.status_text, run.round_info)

        async def on_interject(text: str) -> None:
            """토론이 도는 중에 들어온 입력.

            새 턴을 열지 않고(러너는 세션당 하나만 돌립니다) 진행 중인 토론의
            다음 발언 차례에 유저 발언으로 끼워 넣습니다.
            """
            if not current_session_id or not runner.interject(current_session_id, text):
                # 마지막 발언과 화면 갱신 사이에 눌린 경우. 글을 삼키지 않습니다.
                chat_feed.restore_input(text)
                chat_feed.set_busy(False, "진행 중인 토론이 없습니다", "Ready")
                ui.notify(
                    "토론이 이미 끝나 개입을 전달하지 못했습니다. 그대로 다시 보내면 새 턴으로 진행됩니다.",
                    type="warning",
                    position="bottom-right",
                )

        async def on_stop() -> None:
            """남은 라운드를 접고 지금까지의 토론으로 마무리하도록 요청합니다."""
            if not current_session_id or not runner.request_stop(current_session_id):
                chat_feed.set_busy(False, "진행 중인 토론이 없습니다", "Ready")
                return
            # 화면 갱신과 알림은 러너가 돌려주는 stop_requested 이벤트에서 합니다.
            # 이 세션을 보고 있는 다른 화면도 같은 경로로 알게 됩니다.

        # ------------------------------------------------------------ 레이아웃

        sidebar = SessionSidebar(on_session_selected, on_new_session)
        drawer = sidebar.build_ui()

        roster_control = AgentRosterControl(on_config_changed, on_resync_agents)
        chat_feed = ChatFeed(on_send_message, on_interject=on_interject, on_stop=on_stop)
        artifact_viewer = ArtifactViewer()

        # ------------------------------------------------------------ 정보 창

        # 다이얼로그는 페이지를 만들 때 **한 번만** 짓고, 버튼은 열기만 합니다.
        # 누를 때마다 새로 만들면 그때의 슬롯 컨텍스트에 붙어 화면에 뜨지 않거나
        # 눌린 횟수만큼 DOM 에 쌓입니다.
        with ui.dialog() as about_dialog, ui.card().classes(
            "bg-slate-900 border border-slate-700 rounded-xl p-0 w-[420px] max-w-[92vw]"
        ):
            with ui.column().classes("w-full gap-0"):
                # 머리
                with ui.row().classes(
                    "w-full items-center gap-2.5 px-5 pt-5 pb-3 border-b border-slate-800"
                ):
                    ui.icon("forum", size="md").classes("text-indigo-400")
                    with ui.column().classes("gap-0 flex-grow"):
                        ui.label(APP_SHORT_NAME).classes(
                            "text-lg font-bold text-white tracking-wide")
                        ui.label(APP_TAGLINE).classes("text-[11px] text-slate-400")
                    ui.badge(APP_VERSION_LABEL, color="indigo-8").props("dense")

                # 본문
                with ui.column().classes("w-full gap-2 px-5 py-4"):
                    for _label, _value, _icon in (
                        ("Author", AUTHOR, "person"),
                        ("Email", AUTHOR_EMAIL, "mail"),
                        ("Version", APP_VERSION_LABEL, "sell"),
                        ("License", LICENSE_NAME, "gavel"),
                    ):
                        with ui.row().classes("items-center gap-2.5 w-full flex-nowrap"):
                            ui.icon(_icon, size="xs").classes("text-slate-500")
                            ui.label(_label).classes(
                                "text-[11px] uppercase tracking-wider text-slate-500 w-16 shrink-0")
                            # select-all: 마우스로 한 번 눌러 전체를 집을 수 있게.
                            ui.label(_value).classes(
                                "text-sm text-slate-200 font-medium select-all break-all")

                # 꼬리 — 한 줄 복사
                with ui.row().classes(
                    "w-full items-center justify-between gap-2 px-5 py-3 "
                    "border-t border-slate-800 bg-slate-950/40 rounded-b-xl"
                ):
                    copy_btn = ui.button("정보 복사", icon="content_copy",
                                         on_click=lambda: _copy_about())
                    copy_btn.props("flat dense color=grey-5").classes("text-xs")
                    close_btn = ui.button("닫기", on_click=about_dialog.close)
                    close_btn.props("flat dense color=indigo-4").classes("text-xs")

        def _copy_about() -> None:
            copy_to_clipboard(ABOUT_LINE)
            ui.notify("정보를 복사했습니다.", type="positive", position="bottom-right")

        # Top Header
        with ui.header().classes("bg-slate-900 border-b border-slate-800 px-4 py-2 items-center justify-between"):
            with ui.row().classes("items-center gap-2.5"):
                ui.button(icon="menu", on_click=drawer.toggle).props("flat dense round color=grey-4").tooltip("사이드바 열기/닫기")
                ui.icon("forum", size="sm").classes("text-indigo-400 mr-0.5")
                with ui.column().classes("gap-0"):
                    with ui.row().classes("items-center gap-1.5"):
                        ui.label(APP_NAME).classes("text-base font-bold text-white tracking-wide")
                        ui.badge(APP_VERSION_LABEL, color="slate-7").props("dense outline")
                    ui.label(APP_TAGLINE).classes("text-[11px] text-slate-400")

            with ui.row().classes("items-center gap-2"):
                ui.badge("FastAPI + NiceGUI", color="indigo-8").props("dense")
                ui.badge("MCP Host", color="teal-8").props("dense")
                ui.badge("LiteLLM Multi-Model", color="purple-8").props("dense")
                info_btn = ui.button(icon="info", on_click=about_dialog.open)
                info_btn.props("flat dense round color=grey-4")
                info_btn.tooltip(f"만든 사람 · 버전 — {APP_VERSION_LABEL}")

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
                    parallel_limit=roster_control.parallel_limit,
                    active_agents=roster_control.get_active_agent_keys(),
                    known_agents=roster_control.known_agent_keys(),
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
                    pool = roster_control.agent_pool
                    personas = await effective_personas(db, sid, pool)
                    # 잠긴 대화는 잠글 때 굳은 에이전트로 로스터를 그립니다.
                    # conf.json 에서 지워진 에이전트도 이 대화에서는 계속 발언합니다.
                    frozen = await session_roster_agents(db, s_obj, pool)
                    roster_control.load_from_session(
                        active_keys=s_obj.active_agents or [],
                        known_keys=s_obj.known_agents or [],
                        strategy=resolve_strategy_name(s_obj.strategy),
                        max_rounds=s_obj.max_rounds or 3,
                        parallel_limit=s_obj.parallel_limit or 3,
                        instructions=s_obj.custom_instructions or "",
                        session_id=sid,
                        personas_locked=bool(s_obj.personas_locked),
                        personas=personas,
                        workspace_dir=s_obj.workspace_dir or "",
                        session_agents=frozen,
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
                                "output": clip_tool_output(tc.output),
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

            roster_control.refresh_mcp_lock()

            if running:
                chat_feed.set_busy(run.busy, run.status_text, run.round_info)
                # 새로고침 뒤에도 "정지 중" 이라는 사실이 남아 있어야, 이미 접수된
                # 요청을 다시 누르지 않습니다.
                chat_feed.set_stop_pending(bool(snapshot["stop_requested"]))
                attach_to_run(run)
            else:
                chat_feed.set_busy(False, "대기 중", "Ready")

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

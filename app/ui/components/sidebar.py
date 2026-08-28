import asyncio
import logging
from typing import Callable, Coroutine, List, Optional
from nicegui import ui
from sqlalchemy import desc, select, delete
from app.database.models import ArtifactModel, MessageModel, SessionModel, ToolCallRecordModel
from app.database.session import get_session_factory
from app.orchestration.runner import get_debate_runner

logger = logging.getLogger(__name__)


class SessionSidebar:
    """Manages the session list sidebar, session creation, switching, renaming, and deletion."""

    def __init__(
        self,
        on_session_selected: Callable[[str], Coroutine[None, None, None]],
        on_new_session: Callable[[], Coroutine[None, None, None]],
    ):
        self.on_session_selected = on_session_selected
        self.on_new_session = on_new_session
        self.current_session_id: Optional[str] = None
        self.container: Optional[ui.column] = None
        self.drawer: Optional[ui.left_drawer] = None
        self.session_factory = get_session_factory()

    def build_ui(self) -> ui.left_drawer:
        self.drawer = ui.left_drawer(value=True, elevated=True).classes(
            "bg-slate-900 text-slate-100 p-3.5 border-r border-slate-800 flex flex-col justify-between"
        ).props("width=290")

        with self.drawer:
            with ui.column().classes("w-full flex-grow overflow-hidden"):
                with ui.row().classes("w-full items-center justify-between mb-3 px-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("forum", size="md").classes("text-indigo-400")
                        ui.label("Debate Sessions").classes("text-base font-bold tracking-wide")
                    ui.button(
                        icon="add",
                        on_click=self._handle_create_new,
                    ).props("flat round dense color=indigo-4").tooltip("새 세션 시작")

                ui.button(
                    "+ New Debate Chat",
                    icon="chat",
                    on_click=self._handle_create_new,
                ).props("unelevated color=indigo-6").classes("w-full mb-3 font-semibold shadow-md text-xs py-2")

                ui.separator().classes("bg-slate-800 mb-2.5")

                ui.label("세션 목록").classes("text-xs font-semibold text-slate-400 mb-2 px-1 tracking-wider")

                # Scrollable session list with adequate right padding so borders never clip
                with ui.scroll_area().classes("w-full flex-grow h-[calc(100vh-220px)] pr-2 py-1"):
                    self.container = ui.column().classes("w-full gap-2.5 p-0.5")

        return self.drawer

    async def _handle_create_new(self) -> None:
        await self.on_new_session()
        await self.refresh_list()

    async def refresh_list(self) -> None:
        """Reloads session items from database and renders them."""
        if not self.container:
            return

        self.container.clear()

        async with self.session_factory() as db:
            stmt = select(SessionModel).order_by(desc(SessionModel.updated_at))
            res = await db.execute(stmt)
            sessions = res.scalars().all()

        if not sessions:
            with self.container:
                ui.label("생성된 세션이 없습니다.").classes("text-sm text-slate-500 italic p-2")
            return

        runner = get_debate_runner()

        with self.container:
            for s in sessions:
                is_active = (s.id == self.current_session_id)
                is_running = runner.is_running(s.id)
                card_classes = (
                    "w-full p-2.5 rounded-lg transition-all box-border "
                    + ("bg-indigo-950/90 border-2 border-indigo-400 text-white shadow-lg" if is_active else "bg-slate-800/70 hover:bg-slate-800 text-slate-300 border border-slate-700/60")
                )

                with ui.card().classes(card_classes):
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        # Clickable session selection area
                        with ui.column().classes("flex-grow cursor-pointer gap-0 min-w-0 mr-1").on(
                            "click", lambda _, sid=s.id: self._select_session(sid)
                        ):
                            with ui.row().classes("w-full items-center gap-1.5 no-wrap"):
                                if is_running:
                                    # 다른 화면에 있어도 토론은 계속됩니다. 어느 세션이
                                    # 돌고 있는지 목록에서 바로 보이게 합니다.
                                    ui.spinner("dots", size="xs", color="indigo-4")
                                # 이름 변경은 오른쪽 연필 버튼과 제목 더블클릭 둘 다로 됩니다.
                                # 버튼은 카드 폭이 좁을 때 눈에 잘 띄지 않습니다.
                                title_label = ui.label(s.title or "Untitled Debate").classes(
                                    "text-xs font-semibold truncate"
                                )
                                title_label.on(
                                    "dblclick", lambda _, s_obj=s: self._show_rename_dialog(s_obj)
                                )
                                title_label.tooltip("더블클릭하면 이름을 바꿉니다")
                            
                            with ui.row().classes("w-full items-center justify-between mt-1 text-xs text-slate-400"):
                                date_str = s.created_at.strftime("%m-%d %H:%M") if s.created_at else ""
                                ui.label(date_str).classes("text-[10px]")
                                
                                agents_count = len(s.active_agents) if s.active_agents else 0
                                ui.badge(f"{agents_count} Agents", color="slate-700").props("dense text-[9px] text-color=grey-3")

                        # Action Buttons (Edit / Delete) - Isolated from card click
                        with ui.row().classes("items-center gap-0.5 flex-shrink-0"):
                            ui.button(
                                icon="edit",
                                on_click=lambda _, s_obj=s: self._show_rename_dialog(s_obj),
                            ).props("flat round dense size=xs color=grey-4").tooltip("이름 변경 (제목 더블클릭도 같습니다)")
                            
                            ui.button(
                                icon="delete",
                                on_click=lambda _, sid=s.id: self._show_delete_dialog(sid),
                            ).props("flat round dense size=xs color=red-4").tooltip("삭제")

    async def _select_session(self, session_id: str) -> None:
        self.current_session_id = session_id
        await self.on_session_selected(session_id)
        await self.refresh_list()

    def _show_rename_dialog(self, session_obj: SessionModel) -> None:
        with ui.dialog() as dialog, ui.card().classes("p-4 w-96 bg-slate-900 text-white border border-slate-700"):
            ui.label("세션 이름 변경").classes("text-lg font-bold mb-2")
            name_input = ui.input(value=session_obj.title or "").props("outlined dense dark").classes("w-full mb-4")

            async def do_rename():
                new_title = name_input.value.strip()
                if new_title:
                    async with self.session_factory() as db:
                        stmt = select(SessionModel).where(SessionModel.id == session_obj.id)
                        res = await db.execute(stmt)
                        curr = res.scalar_one_or_none()
                        if curr:
                            curr.title = new_title
                            await db.commit()
                    dialog.close()
                    ui.notify("세션 이름이 변경되었습니다.", type="positive")
                    await self.refresh_list()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dialog.close).props("flat color=grey")
                ui.button("저장", on_click=do_rename).props("unelevated color=indigo-6")

        dialog.open()

    def _show_delete_dialog(self, session_id: str) -> None:
        with ui.dialog() as dialog, ui.card().classes("p-4 w-80 bg-slate-900 text-white border border-slate-700"):
            ui.label("세션 삭제").classes("text-lg font-bold text-red-400 mb-2")
            ui.label("이 세션과 모든 대화 내역 및 산출물이 삭제됩니다. 계속하시겠습니까?").classes("text-sm text-slate-300 mb-4")

            async def do_delete():
                # 이 세션의 토론이 백그라운드에서 돌고 있으면 먼저 세웁니다.
                # 그러지 않으면 방금 지운 세션에 발언을 기록하려다 실패합니다.
                get_debate_runner().forget(session_id)

                async with self.session_factory() as db:
                    await db.execute(delete(ToolCallRecordModel).where(ToolCallRecordModel.session_id == session_id))
                    await db.execute(delete(MessageModel).where(MessageModel.session_id == session_id))
                    await db.execute(delete(ArtifactModel).where(ArtifactModel.session_id == session_id))
                    await db.execute(delete(SessionModel).where(SessionModel.id == session_id))
                    await db.commit()

                dialog.close()
                ui.notify("세션이 삭제되었습니다.", type="info")

                if self.current_session_id == session_id:
                    self.current_session_id = None
                    # Load another latest session or create new
                    async with self.session_factory() as db:
                        stmt = select(SessionModel).order_by(desc(SessionModel.updated_at)).limit(1)
                        res = await db.execute(stmt)
                        next_sess = res.scalar_one_or_none()

                    if next_sess:
                        self.current_session_id = next_sess.id
                        await self.on_session_selected(next_sess.id)
                    else:
                        await self.on_new_session()

                await self.refresh_list()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("취소", on_click=dialog.close).props("flat color=grey")
                ui.button("삭제", on_click=do_delete).props("unelevated color=red-6")

        dialog.open()

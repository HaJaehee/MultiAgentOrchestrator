import json
import logging
from typing import Any, Dict, List, Optional
from nicegui import ui

logger = logging.getLogger(__name__)


class ArtifactViewer:
    """Tabbed artifact viewer for Synthesized Markdown, Code, Mermaid diagrams, and JSON exports."""

    def __init__(self):
        self.artifacts: List[Dict[str, Any]] = []
        self.container: Optional[ui.card] = None
        self.tabs: Optional[ui.tabs] = None
        self.tab_panels: Optional[ui.tab_panels] = None

    def build_ui(self) -> ui.card:
        with ui.card().classes(
            "w-full h-full bg-slate-900 border border-slate-800 p-3 rounded-xl shadow-lg flex flex-col text-slate-100 overflow-hidden"
        ) as self.container:
            # Header
            with ui.row().classes("w-full items-center justify-between pb-2 border-b border-slate-800 flex-shrink-0"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("inventory_2", size="sm").classes("text-indigo-400")
                    ui.label("Artifact & Output Viewer").classes("text-sm font-bold tracking-wide")
                self.count_badge = ui.badge("0 Items", color="slate-700").props("dense")

            # Main content area (Fills vertical space)
            self.content_column = ui.column().classes("w-full flex-grow min-h-0 mt-2 overflow-hidden flex flex-col")
            with self.content_column:
                self._render_empty()

        return self.container

    def _render_empty(self) -> None:
        with ui.column().classes("w-full h-full items-center justify-center py-20 text-center text-slate-500"):
            ui.icon("draw", size="xl").classes("text-slate-700 mb-2")
            ui.label("합성된 산출물이 없습니다").classes("text-sm font-bold text-slate-400")
            ui.label("멀티 에이전트 토론이 종료되면 최종 보고서, 다이어그램, 코드가 이곳에 렌더링됩니다.").classes("text-xs max-w-xs mt-1")

    def render_artifacts(self, artifacts: List[Dict[str, Any]]) -> None:
        self.artifacts = artifacts
        if hasattr(self, "count_badge") and self.count_badge:
            self.count_badge.set_text(f"{len(artifacts)} Items")

        if not self.content_column:
            return

        self.content_column.clear()

        if not artifacts:
            with self.content_column:
                self._render_empty()
            return

        with self.content_column:
            with ui.tabs().classes("w-full text-indigo-400 bg-slate-950/60 rounded-lg flex-shrink-0") as tabs:
                for i, art in enumerate(artifacts):
                    art_type = art.get("artifact_type", "markdown")
                    title = art.get("title", f"Artifact {i+1}")
                    icon = "description"
                    if art_type == "code":
                        icon = "code"
                    elif art_type == "mermaid":
                        icon = "account_tree"
                    elif art_type == "json":
                        icon = "data_object"

                    ui.tab(name=f"tab_{i}", label=title[:18] + ("..." if len(title) > 18 else ""), icon=icon)

            with ui.tab_panels(tabs, value="tab_0").classes("w-full flex-grow bg-transparent p-0 mt-2 min-h-0 overflow-hidden flex flex-col"):
                for i, art in enumerate(artifacts):
                    with ui.tab_panel(f"tab_{i}").classes("p-1 w-full h-full flex flex-col"):
                        self._render_artifact_item(art)

    def _render_artifact_item(self, art: Dict[str, Any]) -> None:
        art_type = art.get("artifact_type", "markdown")
        title = art.get("title", "Artifact")
        content = art.get("content", "")
        language = art.get("language", "markdown")

        with ui.column().classes("w-full h-full gap-2 flex flex-col flex-nowrap overflow-hidden"):
            # Sub-header with copy/download controls
            with ui.row().classes("w-full items-center justify-between bg-slate-800/80 px-3 py-1.5 rounded-lg text-xs flex-shrink-0"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(title).classes("font-bold text-slate-200 truncate max-w-[200px]")
                    ui.badge(art_type.upper(), color="indigo-7").props("dense text-[10px]")

                with ui.row().classes("items-center gap-1"):
                    ui.button(
                        "Copy",
                        icon="content_copy",
                        on_click=lambda _, text=content: self._copy_to_clipboard(text),
                    ).props("flat dense size=sm color=slate-3")
                    ui.button(
                        "Download",
                        icon="download",
                        on_click=lambda _, t=title, c=content, ty=art_type: self._download_artifact(t, c, ty),
                    ).props("flat dense size=sm color=slate-3")

            # Body based on type (Scrolls vertically)
            with ui.scroll_area().classes("w-full flex-grow min-h-0 p-3 bg-slate-950/80 border border-slate-800 rounded-lg"):
                if art_type == "mermaid":
                    try:
                        ui.mermaid(content).classes("w-full")
                    except Exception:
                        ui.code(content, language="mermaid")
                elif art_type == "code":
                    ui.code(content, language=language).classes("w-full text-xs")
                elif art_type == "json":
                    ui.code(content, language="json").classes("w-full text-xs")
                else:
                    with ui.column().classes("prose prose-invert max-w-none text-xs text-slate-200"):
                        ui.markdown(content)

    def _copy_to_clipboard(self, text: str) -> None:
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)});")
        ui.notify("클립보드에 복사되었습니다!", type="positive", position="top")

    def _download_artifact(self, title: str, content: str, art_type: str) -> None:
        ext_map = {"code": "py", "mermaid": "mmd", "json": "json", "markdown": "md"}
        ext = ext_map.get(art_type, "txt")
        clean_title = "".join(c for c in title if c.isalnum() or c in ("-", "_")).rstrip()
        filename = f"{clean_title or 'artifact'}.{ext}"

        ui.download(content.encode("utf-8"), filename)
        ui.notify(f"'{filename}' 다운로드가 시작되었습니다.", type="info", position="top")

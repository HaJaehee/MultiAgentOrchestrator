FAVICON_SVG = (
    'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
    '<path fill="%236366f1" d="M16 3C8.82 3 3 8.148 3 14.5c0 3.23 1.487 6.155 3.924 8.273L5 28.5l6.398-1.828c1.442.538 3.037.828 4.602.828 7.18 0 13-5.148 13-11.5S23.18 3 16 3z"/>'
    '<circle cx="10" cy="14.5" r="1.8" fill="%23ffffff"/>'
    '<circle cx="16" cy="14.5" r="1.8" fill="%23ffffff"/>'
    '<circle cx="22" cy="14.5" r="1.8" fill="%23ffffff"/>'
    '</svg>'
)

CUSTOM_CSS = """
/* Global Styling */
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

/* Chat timeline container styling */
.debate-timeline {
    scroll-behavior: smooth;
}

/* Tool execution accordion styling */
.mcp-tool-accordion {
    border: 1px solid rgba(0, 150, 136, 0.3) !important;
    border-radius: 8px !important;
    background-color: rgba(0, 150, 136, 0.05) !important;
    margin-top: 6px;
    margin-bottom: 6px;
}

.mcp-tool-badge {
    font-family: monospace;
    font-size: 0.82rem;
}

/* Agent Card Active State */
.agent-card-active {
    border: 2px solid #1976d2 !important;
    box-shadow: 0 4px 12px rgba(25, 118, 210, 0.25) !important;
}

.agent-card-inactive {
    opacity: 0.55;
    filter: grayscale(40%);
}

/* Artifact tab panel height */
.artifact-content-box {
    max-height: 650px;
    overflow-y: auto;
}

/* --- 마크다운 제목 크기 ------------------------------------------------
   NiceGUI 에는 Tailwind typography(prose) 가 실려 있지 않아, 마크다운 제목이
   브라우저 기본값으로 나옵니다 (본문 14px 인 카드 안에서 h1 32px, h3 30px).
   제목 한 줄이 카드를 다 차지하므로 본문에 비례하는 크기로 다시 잡습니다.
   em 단위라 채팅 카드(14px)와 산출물 뷰어(12px) 양쪽에서 함께 줄어듭니다. */
.nicegui-markdown h1,
.nicegui-markdown h2,
.nicegui-markdown h3,
.nicegui-markdown h4,
.nicegui-markdown h5,
.nicegui-markdown h6 {
    font-weight: 700;
    line-height: 1.35;
    margin: 0.9em 0 0.4em;
    color: #e2e8f0;
}
.nicegui-markdown h1 { font-size: 1.35em; }
.nicegui-markdown h2 { font-size: 1.2em; }
.nicegui-markdown h3 { font-size: 1.08em; }
.nicegui-markdown h4 { font-size: 1em; }
.nicegui-markdown h5,
.nicegui-markdown h6 { font-size: 0.95em; color: #cbd5e1; }

/* 첫 줄이 제목이면 위 여백이 카드 안에서 떠 보입니다. */
.nicegui-markdown > :first-child { margin-top: 0; }

/* 크기를 줄인 만큼 구분은 밑줄이 대신합니다. */
.nicegui-markdown h1,
.nicegui-markdown h2 {
    border-bottom: 1px solid rgba(148, 163, 184, 0.22);
    padding-bottom: 0.22em;
}

.nicegui-markdown p { margin: 0.5em 0; }
.nicegui-markdown ul,
.nicegui-markdown ol { margin: 0.5em 0; padding-left: 1.35em; }
.nicegui-markdown li { margin: 0.2em 0; }

/* --- Mermaid 다이어그램 --------------------------------------------------
   Mermaid 는 밝은 배경을 전제로 그립니다. 화살표와 글자가 검은색이라 어두운
   카드 위에 그대로 올리면 선이 배경에 묻혀 보이지 않습니다. 주변은 어두운
   테마 그대로 두고 다이어그램만 밝은 판 위에 올립니다. */
.mado-mermaid,
.nicegui-mermaid {
    background: #f8fafc;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    padding: 14px;
    overflow-x: auto;
}
.mado-mermaid svg,
.nicegui-mermaid svg {
    max-width: 100%;
    height: auto;
}

/* Custom Scrollbars */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}
::-webkit-scrollbar-track {
    background: transparent;
}
::-webkit-scrollbar-thumb {
    background: rgba(120, 120, 120, 0.4);
    border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
    background: rgba(120, 120, 120, 0.7);
}
"""

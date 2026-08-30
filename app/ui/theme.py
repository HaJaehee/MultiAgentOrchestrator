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

/* --- 세션 목록의 가로 폭 -------------------------------------------------
   Quasar 스크롤 영역의 내용 상자(.q-scrollarea__content)는 width:auto 라, 그 안의
   `w-full` 이 "보이는 너비" 가 아니라 "내용 너비" 가 됩니다. 그래서 세션 이름이
   길면 카드가 서랍보다 넓게 그려지고, 오른쪽 끝(이름 뒷부분과 수정·저장·삭제
   버튼)이 서랍 밖으로 밀려나 잘렸습니다. 보이는 너비에 맞춰 고정합니다. */
.session-list .q-scrollarea__content {
    width: 100%;
    max-width: 100%;
}

/* --- 에이전트 카드 드래그 -------------------------------------------------
   순서를 바꾸는 동안 무엇을 집었고 어디에 놓이는지 보여야 합니다. 이것이 없으면
   커서를 어디에 두어야 앞이고 어디가 뒤인지 알 방법이 없어, 놓아 보고 결과로
   짐작하게 됩니다. */
.agent-dragging {
    opacity: 0.4;
    cursor: grabbing !important;
}

/* 놓일 자리를 카드 모서리의 굵은 선으로 표시합니다. 카드가 가로로 늘어서므로
   왼쪽 선은 "이 카드 앞", 오른쪽 선은 "이 카드 뒤" 입니다. `box-shadow` 는
   레이아웃을 밀지 않아, 표시가 뜰 때 카드들이 흔들리지 않습니다. */
.agent-drop-before {
    box-shadow: inset 4px 0 0 0 #818cf8;
}
.agent-drop-after {
    box-shadow: inset -4px 0 0 0 #818cf8;
}

/* --- 발언 카드 접기 -------------------------------------------------------
   발언이 끝나면 본문을 세 줄만 남기고 접습니다. 라운드가 몇 번 돌면 카드 하나가
   화면을 다 차지해서, 토론의 흐름을 보려면 계속 스크롤해야 했습니다.

   `max-height` 로 자릅니다. `-webkit-line-clamp` 는 컨테이너를 `-webkit-box` 로
   바꿔야 하는데, 그러면 문단·목록·코드블록이 섞인 마크다운의 블록 배치가 깨집니다.

   잘린 자리는 `mask-image` 로 흐립니다. 배경 그라디언트를 덮는 방식은 카드마다
   배경색이 달라(발언자별 색) 색을 맞춰야 하지만, 마스크는 내용 자체를 투명하게
   만들어 어떤 배경 위에서도 맞습니다. */
.chat-body-clamped {
    max-height: 4.8em;                      /* 본문 줄높이 약 1.6em x 3줄 */
    overflow: hidden;
    -webkit-mask-image: linear-gradient(to bottom, #000 62%, transparent 100%);
    mask-image: linear-gradient(to bottom, #000 62%, transparent 100%);
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

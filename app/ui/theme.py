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

import contextlib
import logging
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from nicegui import app as nicegui_app, ui
import uvicorn
from app.agents.pool import get_agent_pool
from app.config import get_config
from app.database.session import init_db
from app.mcp.manager import get_mcp_manager
from app.orchestration.runner import get_debate_runner
from app.ui.app import create_ui
from app.ui.personas_page import create_personas_page

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("multiagent")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI & NiceGUI application lifecycle manager."""
    logger.info("Starting Multi-Agent Orchestrator Platform...")

    # 1. Load configuration
    cfg = get_config()
    logger.info(f"Loaded configuration for host={cfg.app.host}:{cfg.app.port}, db={cfg.app.db_url}")
    logger.info(f"Web UI: http://{cfg.app.host}:{cfg.app.port}")

    # 2. Initialize Database Tables
    await init_db(cfg.app.db_url)
    logger.info("SQLite database tables initialized.")

    # 3. Initialize MCP Manager & Tool Discovery
    mcp_mgr = get_mcp_manager()
    await mcp_mgr.initialize()
    logger.info("MCP Manager initialized.")

    # 4. Initialize Agent Pool
    agent_pool = get_agent_pool()
    logger.info(f"Agent Pool ready with {len(agent_pool.list_all())} agents.")

    yield

    logger.info("Shutting down Multi-Agent Orchestrator Platform...")

    # 백그라운드로 돌고 있는 토론 태스크를 먼저 세웁니다.
    await get_debate_runner().shutdown()
    logger.info("Background debate tasks cancelled.")

    # 유지 중인 MCP 세션과 서버 프로세스를 정리합니다.
    await mcp_mgr.shutdown()
    logger.info("MCP sessions closed.")


# 1. Create FastAPI Application
server = FastAPI(
    title="Multi-Agent Orchestrator Platform",
    description="MCP-enabled Autonomous Multi-Agent Collaborative Debate & Synthesis Backend",
    version="0.1.0",
    lifespan=lifespan,
)


@server.get("/api/health")
async def health_check():
    cfg = get_config()
    pool = get_agent_pool()
    return {
        "status": "healthy",
        "app": {"host": cfg.app.host, "port": cfg.app.port, "debug": cfg.app.debug},
        "registered_agents": [a.key for a in pool.list_all()],
    }


@server.get("/api/agents")
async def list_agents():
    pool = get_agent_pool()
    return [
        {
            "key": a.key,
            "name": a.name,
            "role": a.role,
            "model": a.model,
            "api_base": a.api_base,
            "api_version": a.api_version,
            "provider": a.provider,
            "has_api_key": bool(a.api_key and a.api_key.strip()),
            "mode": "live" if a.is_live else "unconfigured",
            "temperature": a.temperature,
            "max_tokens": a.max_tokens,
            "sequential_thinking": a.sequential_thinking.model_dump(exclude={"prompt_template"}),
            "allowed_mcp_servers": a.allowed_mcp_servers,
        }
        for a in pool.list_all()
    ]


@server.get("/api/sessions/{session_id}/personas")
async def session_personas(session_id: str):
    """세션에서 실제로 쓰이는 에이전트 페르소나와 잠금 여부."""
    from sqlalchemy import select

    from app.agents.personas import effective_personas
    from app.agents.pool import get_agent_pool
    from app.database.models import SessionModel
    from app.database.session import get_session_factory

    async with get_session_factory()() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        session_model = result.scalar_one_or_none()
        if session_model is None:
            return JSONResponse({"detail": "session not found"}, status_code=404)
        personas = await effective_personas(db, session_id, get_agent_pool())

    return {
        "session_id": session_id,
        "personas_locked": bool(session_model.personas_locked),
        "agents": [p.model_dump() for p in personas.values()],
    }


@server.get("/api/mcp")
async def mcp_status():
    """MCP 서버별 연결 상태. conf.toml 에서 비활성화한 서버도 함께 보고합니다."""
    cfg = get_config()
    status = get_mcp_manager().connection_status()
    return [
        {
            "name": name,
            "enabled": server_cfg.enabled,
            "command": server_cfg.command,
            **(
                status.get(name)
                or {"connected": False, "available": False, "tool_count": 0, "error": None}
            ),
        }
        for name, server_cfg in cfg.mcp_servers.items()
    ]


from app.ui.theme import FAVICON_SVG

# 2. Build NiceGUI Application
create_ui()
create_personas_page()
ui.run_with(
    server,
    title="Multi-Agent Orchestrator Platform",
    favicon=FAVICON_SVG,
    dark=True,
)


def start():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Agent Orchestrator Platform")
    parser.add_argument("--host", type=str, default=None, help="Host to bind (overrides conf.toml and .env)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (overrides conf.toml and .env)")
    parser.add_argument("--config", type=str, default="conf.toml", help="Path to config file")
    parser.add_argument("--reload", action="store_true", default=None, help="Enable auto-reload")
    parser.add_argument("--no-reload", action="store_true", default=False, help="Disable auto-reload")
    args, _ = parser.parse_known_args()

    cfg = get_config(config_path=args.config)
    host = args.host if args.host is not None else cfg.app.host
    port = args.port if args.port is not None else cfg.app.port

    # Keep cfg.app in sync with actual bound host/port
    cfg.app.host = host
    cfg.app.port = port

    do_reload = cfg.app.debug
    if args.no_reload:
        do_reload = False
    elif args.reload:
        do_reload = True

    uvicorn.run(
        "app.main:server",
        host=host,
        port=port,
        reload=do_reload,
        reload_dirs=["app"] if do_reload else None,
        reload_excludes=["workspace", "workspace/*", "*.db*", "*.db-wal", "*.db-shm", "mcp_sandbox/*", ".git/*"] if do_reload else None,
    )


if __name__ == "__main__":
    start()


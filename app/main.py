import contextlib
import logging
from typing import AsyncGenerator
from fastapi import FastAPI
from nicegui import app as nicegui_app, ui
import uvicorn
from app.agents.pool import get_agent_pool
from app.config import get_config
from app.database.session import init_db
from app.mcp.manager import get_mcp_manager
from app.ui.app import create_ui

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
            "mode": "live" if a.is_live else "simulation",
            "temperature": a.temperature,
            "max_tokens": a.max_tokens,
            "sequential_thinking": a.sequential_thinking.model_dump(exclude={"prompt_template"}),
            "allowed_mcp_servers": a.allowed_mcp_servers,
        }
        for a in pool.list_all()
    ]


from app.ui.theme import FAVICON_SVG

# 2. Build NiceGUI Application
create_ui()
ui.run_with(
    server,
    title="Multi-Agent Orchestrator Platform",
    favicon=FAVICON_SVG,
    dark=True,
)


def start():
    cfg = get_config()
    uvicorn.run(
        "app.main:server",
        host=cfg.app.host,
        port=cfg.app.port,
        reload=cfg.app.debug,
    )


if __name__ == "__main__":
    start()

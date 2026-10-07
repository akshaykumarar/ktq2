"""FastAPI entrypoint for Configurable Multi-Agent Chatbot product."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config.settings import load_config
from backend.app.agents.factory import AgentFactory
from backend.app.api.chat import router as chat_router
from backend.app.api.rfi import router as rfi_router
from backend.app.api.vendor import router as vendor_router
from backend.app.db.health import check_database_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager to initialize agent registry on startup."""
    logger.info("Initializing multi-agent system from configuration...")
    config = load_config()
    registry = AgentFactory.create_registry(config)
    app.state.config = config
    app.state.agent_registry = registry
    logger.info(
        "Agent registry successfully initialized with specialists: %s",
        list(registry.specialists.keys()),
    )
    yield
    logger.info("Shutting down multi-agent backend...")


app = FastAPI(
    title="AI-QL Multi-Agent Procurement API",
    description="Configurable multi-agent system built on FastAPI and PydanticAI.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local UI and external origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(chat_router)
app.include_router(rfi_router)
app.include_router(vendor_router)


@app.get("/health", tags=["system"])
@app.get("/api/health", tags=["system"])
async def health_check() -> dict:
    """System health check endpoint."""
    registry = getattr(app.state, "agent_registry", None)
    specialists = list(registry.specialists.keys()) if registry else []
    return {
        "status": "ok",
        "agents": ["master"] + specialists,
    }


@app.get("/health/db", tags=["system"])
@app.get("/api/db/health", tags=["system"])
async def database_health_check() -> dict:
    """Check configured Postgres connectivity and schema availability."""
    config = getattr(app.state, "config", None) or load_config()
    return check_database_connection(config.secrets)


# Mount existing chatbot UI frontend if available
chatbot_dir = Path.cwd() / "chatbot"
if not chatbot_dir.exists():
    chatbot_dir = Path(__file__).resolve().parent.parent.parent / "chatbot"

if chatbot_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(chatbot_dir), html=True), name="chatbot_ui")

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Redirect root path to chatbot UI."""
        return RedirectResponse(url="/ui/")

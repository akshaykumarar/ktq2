"""Chat API endpoint handling multi-agent conversation requests."""

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.app.agents.factory import AgentRegistry
from backend.app.agents.master import run_master_orchestration

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """Payload sent by chat interface."""

    message: str = Field(..., min_length=1, description="User prompt or inquiry")
    conversation_id: Optional[str] = Field(
        default=None,
        description="Optional session or conversation ID",
    )


class ChatResponse(BaseModel):
    """Response returned to chat interface."""

    message: str = Field(..., description="Assistant response text")
    agent: str = Field(..., description="Name of the specialist or master agent answering")
    conversation_id: str = Field(..., description="Session identifier")


def get_agent_registry(request: Request) -> AgentRegistry:
    """Dependency retrieving the loaded AgentRegistry from app state."""
    registry: AgentRegistry = getattr(request.app.state, "agent_registry", None)
    if not registry:
        raise HTTPException(
            status_code=500,
            detail="Agent registry is not initialized on application startup.",
        )
    return registry


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    payload: ChatRequest,
    registry: AgentRegistry = Depends(get_agent_registry),
) -> ChatResponse:
    """Handle chat interactions and delegate to the appropriate specialist agent.

    Args:
        payload: ChatRequest with user message and optional conversation_id.
        registry: Injected AgentRegistry dependency.

    Returns:
        ChatResponse containing the reply message, agent identifier, and conversation_id.
    """
    conv_id = payload.conversation_id or f"conv-{uuid.uuid4().hex[:8]}"

    try:
        reply_text, agent_name = await run_master_orchestration(
            master_agent=registry.master,
            specialists=registry.specialists,
            user_message=payload.message,
        )
        return ChatResponse(
            message=reply_text,
            agent=agent_name,
            conversation_id=conv_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Agent orchestration failed: {str(exc)}",
        )

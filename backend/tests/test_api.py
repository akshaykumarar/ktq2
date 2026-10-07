"""Tests for FastAPI chat endpoint."""

import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from pydantic_ai.models.test import TestModel

from backend.app.main import app


def test_health_endpoint() -> None:
    """Test health check returns status ok and agent list."""
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert "master" in data["agents"]

    asyncio.run(_test())


def test_chat_endpoint_rfx_flow() -> None:
    """Test end-to-end /api/chat invocation delegating to RFX agent."""
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Pre-initialize app lifespan
            async with app.router.lifespan_context(app):
                # Ensure test models for offline test execution
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                payload = {
                    "message": "Create an RFX for 200 laptops",
                    "conversation_id": "test-session-123",
                }
                response = await client.post("/api/chat", json=payload)
                assert response.status_code == 200
                data = response.json()

                assert data["agent"] == "rfx"
                assert data["conversation_id"] == "test-session-123"
                assert "RFX ID" in data["message"]
                assert "200" in data["message"]

    asyncio.run(_test())

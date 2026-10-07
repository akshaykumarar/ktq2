"""Tests for conversational Packaging RFI creation workflow in Chat API."""

import asyncio
import re
import pytest
from httpx import AsyncClient, ASGITransport
from pydantic_ai.models.test import TestModel

from backend.app.main import app
from backend.app.agents.master import reset_chat_session, get_chat_session


def test_full_rfi_chat_flow() -> None:
    """Test multi-turn conversational workflow for RFI creation:
    1. Select 'Create an RFI' -> Guides requirement collection without premature creation.
    2. Provide packaging specs -> Extracts dimensions, quantity, location, creates draft RFI.
    3. Ask for existing RFI status -> Discouraged and redirected back to RFI creation.
    4. Ask for external vendor info -> Discouraged and redirected back to RFI creation.
    5. Trigger RFI -> Successfully triggers active RFI.
    """
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                # Ensure test models
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-rfi-session-abc"
                reset_chat_session(session_id)

                # Turn 1: Select "Create an RFI"
                turn1_resp = await client.post("/api/chat", json={
                    "message": "Create an RFI",
                    "conversation_id": session_id,
                })
                assert turn1_resp.status_code == 200
                data1 = turn1_resp.json()
                assert data1["agent"] == "rfx"
                assert "Packaging RFI" in data1["message"]
                assert "Dimensions" in data1["message"]
                assert "Quantity" in data1["message"]
                # Must not have prematurely created a dummy record
                assert "RFX ID: RFX-" not in data1["message"]

                # Verify session state
                session = get_chat_session(session_id)
                assert session.is_rfi_workflow is True

                # Turn 2: Provide packaging specifications (screenshot scenario)
                turn2_resp = await client.post("/api/chat", json={
                    "message": "I need 10 corrugated boxes, 300 x 200 x 150 mm, delivered to bangalore by November 15, 2026.",
                    "conversation_id": session_id,
                })
                assert turn2_resp.status_code == 200
                data2 = turn2_resp.json()
                assert data2["agent"] == "rfx"
                assert "RFI Solution & Progress" in data2["message"]
                assert "Corrugated Boxes" in data2["message"] or "corrugated boxes" in data2["message"].lower()
                assert "10 pcs" in data2["message"]
                assert "300 x 200 x 150 mm" in data2["message"]
                assert "Bangalore" in data2["message"]
                assert session.active_rfi_id is not None

                # Turn 3: Attempt to fetch status of existing RFI -> DO NOT ENCOURAGE
                turn3_resp = await client.post("/api/chat", json={
                    "message": "What is the status of RFX-101?",
                    "conversation_id": session_id,
                })
                assert turn3_resp.status_code == 200
                data3 = turn3_resp.json()
                assert "focused exclusively on creating your new RFI" in data3["message"]
                assert "not supported in this workflow" in data3["message"]

                # Turn 3b: Attempt to check vendor response -> DO NOT ENCOURAGE
                turn3b_resp = await client.post("/api/chat", json={
                    "message": "Check vendor response",
                    "conversation_id": session_id,
                })
                assert turn3b_resp.status_code == 200
                data3b = turn3b_resp.json()
                assert "focused exclusively on creating your new RFI" in data3b["message"]

                # Turn 4: Attempt to fetch other information (vendors) -> DO NOT ENCOURAGE
                turn4_resp = await client.post("/api/chat", json={
                    "message": "Who are the vendors in Bangalore?",
                    "conversation_id": session_id,
                })
                assert turn4_resp.status_code == 200
                data4 = turn4_resp.json()
                assert "dedicated exclusively to creating your RFI" in data4["message"]
                assert "Fetching vendor directories or external procurement information is not supported" in data4["message"]

                # Turn 5: Trigger RFI -> Supported progress
                turn5_resp = await client.post("/api/chat", json={
                    "message": "Trigger RFI",
                    "conversation_id": session_id,
                })
                assert turn5_resp.status_code == 200
                data5 = turn5_resp.json()
                assert "TRIGGERED" in data5["message"]

    asyncio.run(_test())


def test_non_rfi_initial_flow_works_normally() -> None:
    """Test that if the user does NOT select RFI initially, vendor response flow operates normally."""
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-vendor-session-xyz"
                reset_chat_session(session_id)

                resp = await client.post("/api/chat", json={
                    "message": "Check vendor response",
                    "conversation_id": session_id,
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["agent"] == "status"
                assert "Vendor" in data["message"] or "RFX" in data["message"]

    asyncio.run(_test())


def test_multi_line_items_rfi_chat_flow() -> None:
    """Test that a request with multiple packaging items (e.g. boxes + tape) extracts all line items."""
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-multi-item-session-123"
                reset_chat_session(session_id)

                # Turn 1: Select Create an RFI
                await client.post("/api/chat", json={
                    "message": "Create an RFI",
                    "conversation_id": session_id,
                })

                # Turn 2: Exact user query with 2 items
                multi_prompt = "I need 1000 corrugated boxes, 300 x 200 x 150 mm, and 300 rolls of brown tape delivered to bangalore by November 15, 2026."
                resp = await client.post("/api/chat", json={
                    "message": multi_prompt,
                    "conversation_id": session_id,
                })
                assert resp.status_code == 200
                data = resp.json()
                msg = data["message"]

                # Assert both line items are present
                assert "| 1 |" in msg
                assert "| 2 |" in msg
                assert "Corrugated Boxes" in msg or "corrugated boxes" in msg.lower()
                assert "1000 pcs" in msg
                assert "300 x 200 x 150 mm" in msg
                assert "Brown Tape" in msg or "brown tape" in msg.lower()
                assert "300 rolls" in msg
                assert "Bangalore" in msg

    asyncio.run(_test())


def test_user_scenario_single_rfi_add_remove_and_duplicates() -> None:
    """Test the exact scenario reported by the user:
    1. Multi-item input: 'i want a 5m stretch films 1000 pcs, 50inch cube boxes 300 and brown tapes 200 pcs, and transparent tapes 200 pcs, bubble wrap 50kg'
       - Correctly extracts:
         - 5m stretch film, 1000 pcs
         - 50 inch cube boxes, 300 boxes / pcs, dimensions 50x50x50 inch
         - brown tape, 200 pcs
         - transparent tape, 200 pcs
         - bubble wrap, 50 kg
       - Creates initial draft (e.g. RFI-#1001)
    2. Adding a duplicate item (e.g. 'bubble wrap 50kg')
       - System flags duplicate item and asks for confirmation
       - Replying 'Yes' adds it to the SAME RFI
    3. Removing an item (e.g. 'remove item 3' or 'remove brown tape')
       - Item is removed from the SAME RFI and remaining items are re-indexed
    """
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-user-scenario-single-rfi"
                reset_chat_session(session_id)

                # Turn 1: Select Create an RFI
                await client.post("/api/chat", json={
                    "message": "Create an RFI",
                    "conversation_id": session_id,
                })

                # Turn 2: Exact user query
                user_prompt = "i want a 5m stretch films 1000 pcs, 50inch cube boxes 300 and brown tapes 200 pcs, and transparent tapes 200 pcs, bubble wrap 50kg"
                resp1 = await client.post("/api/chat", json={
                    "message": user_prompt,
                    "conversation_id": session_id,
                })
                assert resp1.status_code == 200
                data1 = resp1.json()
                msg1 = data1["message"]

                # Extract initial RFI ID
                rfi_id_match = re.search(r"RFI-#(\d+)", msg1)
                assert rfi_id_match is not None
                initial_rfi_id = rfi_id_match.group(1)

                # Verify line items parsed accurately
                assert "5m Stretch Film" in msg1 or "5m stretch film" in msg1.lower()
                assert "1000 pcs" in msg1
                assert "Cube Boxes" in msg1 or "cube boxes" in msg1.lower()
                assert "300 boxes" in msg1 or "300 pcs" in msg1
                assert "50 x 50 x 50 inch" in msg1
                assert "Brown Tape" in msg1 or "brown tape" in msg1.lower()
                assert "Transparent Tape" in msg1 or "transparent tape" in msg1.lower()
                assert "Bubble Wrap" in msg1 or "bubble wrap" in msg1.lower()
                assert "50 kg" in msg1

                # Turn 3: User inputs duplicate item -> Duplicate confirmation triggered
                resp2 = await client.post("/api/chat", json={
                    "message": "bubble wrap 50kg",
                    "conversation_id": session_id,
                })
                assert resp2.status_code == 200
                data2 = resp2.json()
                msg2 = data2["message"]
                assert "Duplicate Item Detected" in msg2
                assert "bubble wrap" in msg2.lower()
                assert "Reply **Yes**" in msg2

                # Turn 4: User confirms addition
                resp3 = await client.post("/api/chat", json={
                    "message": "Yes, please add it",
                    "conversation_id": session_id,
                })
                assert resp3.status_code == 200
                data3 = resp3.json()
                msg3 = data3["message"]

                # Must still be the SAME RFI ID!
                assert f"RFI-#{initial_rfi_id}" in msg3
                assert "| 6 |" in msg3  # 5 items + 1 added = 6 items

                # Turn 5: User removes an item ('remove item 3')
                resp4 = await client.post("/api/chat", json={
                    "message": "remove item 3",
                    "conversation_id": session_id,
                })
                assert resp4.status_code == 200
                data4 = resp4.json()
                msg4 = data4["message"]

                # Must still be the SAME RFI ID!
                assert f"RFI-#{initial_rfi_id}" in msg4
                assert "removed '3'" in msg4 or "removed" in msg4.lower()
                # Total items reduced to 5
                assert "| 5 |" in msg4
                assert "| 6 |" not in msg4

    asyncio.run(_test())



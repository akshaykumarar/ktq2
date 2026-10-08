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
                assert "removed" in msg4.lower()
                # Total items reduced to 5
                assert "| 5 |" in msg4
                assert "| 6 |" not in msg4

    asyncio.run(_test())


def test_commercial_terms_not_created_as_line_items() -> None:
    """Test that commercial terms, headers, and separator lines in intake text are NOT parsed as line items."""
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-terms-filtering-session"
                reset_chat_session(session_id)

                # Intake text mimicking the user's screenshot with packaging items followed by divider and commercial terms
                intake_prompt = """[Pkg-001] Corrugated Boxes 300x200x150 mm 500 pcs
[Pkg-002] BOPP Transparent Tape 200 rolls
[Pkg-003] Bubble Lined Kraft Mailers #4 (10X15 In) 480 pcs
[Pkg-004] Biodegradable Loose Fill Peanuts (10 Cu Ft) 50 bags
------------------------------------------------------------
Mandatory Commercial Terms To Include:
- Clear Indication of MOQ (Minimum Order Quantity) Per Line Item
- Freight / Shipping Cost (Ex-Works / Delivery Included / % Extra)
- Applicable Warranty SLA & Transit Damage Replacement Guarantee
- Volume Discounts, Payment Terms: Net 30 Days
Currency (INR / USD)
Delivered to Bangalore by November 15, 2026"""

                resp = await client.post("/api/chat", json={
                    "message": intake_prompt,
                    "conversation_id": session_id,
                })
                assert resp.status_code == 200
                data = resp.json()
                msg = data["message"]

                # Verify only the 4 packaging items were created as line items
                assert "| 1 |" in msg
                assert "| 2 |" in msg
                assert "| 3 |" in msg
                assert "| 4 |" in msg
                assert "| 5 |" not in msg  # Terms lines MUST NOT become items 5..10!

                # Verify line items contain products
                assert "Corrugated Boxes" in msg or "corrugated boxes" in msg.lower()
                assert "Bopp Transparent Tape" in msg or "transparent tape" in msg.lower()
                assert "Bubble Lined Kraft Mailers" in msg or "kraft mailers" in msg.lower()
                assert "Loose Fill Peanuts" in msg or "peanuts" in msg.lower()

                # Verify separator and terms are NOT in line item rows
                assert "Mandatory Commercial Terms" not in msg.split("#### 📦 Line Items")[1].split("#### 📑 Commercial Terms")[0]
                assert "Clear Indication Of Moq" not in msg.split("#### 📦 Line Items")[1].split("#### 📑 Commercial Terms")[0]
                assert "Freight / Shipping Cost" not in msg.split("#### 📦 Line Items")[1].split("#### 📑 Commercial Terms")[0]

                # Verify commercial terms are captured in Commercial Terms section
                assert "Net 30 Days" in msg
                assert "Bangalore" in msg

    asyncio.run(_test())


def test_range_and_multi_item_removal_and_updates() -> None:
    """Test removing items by range (e.g. 'remove items 3 to 5'), comma list, relative position, keyword, and updating item qty."""
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-range-removal-session"
                reset_chat_session(session_id)

                # Step 1: Create RFI with 6 items
                prompt = "I need 100 boxes 10x10x10, 200 boxes 20x20x20, 300 boxes 30x30x30, 400 tapes, 500 films, 600 mailers"
                resp1 = await client.post("/api/chat", json={
                    "message": prompt,
                    "conversation_id": session_id,
                })
                assert resp1.status_code == 200
                msg1 = resp1.json()["message"]
                assert "| 6 |" in msg1

                # Step 2: Remove items by range: "remove items 3 to 5" (removes 3 items: 3, 4, 5)
                resp2 = await client.post("/api/chat", json={
                    "message": "remove items 3 to 5",
                    "conversation_id": session_id,
                })
                assert resp2.status_code == 200
                msg2 = resp2.json()["message"]
                assert "items 3 to 5" in msg2
                # Remaining should be 3 items, re-indexed 1..3
                assert "| 1 |" in msg2
                assert "| 2 |" in msg2
                assert "| 3 |" in msg2
                assert "| 4 |" not in msg2

                # Step 3: Remove last item: "remove last item"
                resp3 = await client.post("/api/chat", json={
                    "message": "remove last item",
                    "conversation_id": session_id,
                })
                assert resp3.status_code == 200
                msg3 = resp3.json()["message"]
                # Remaining should be 2 items
                assert "| 1 |" in msg3
                assert "| 2 |" in msg3
                assert "| 3 |" not in msg3

                # Step 4: Update item quantity: "update item 1 quantity to 1500"
                resp4 = await client.post("/api/chat", json={
                    "message": "update item 1 quantity to 1500",
                    "conversation_id": session_id,
                })
                assert resp4.status_code == 200
                msg4 = resp4.json()["message"]
                assert "1500" in msg4

                # Step 5: Add new item: "add 800 rolls of brown tape"
                resp5 = await client.post("/api/chat", json={
                    "message": "add 800 rolls of brown tape",
                    "conversation_id": session_id,
                })
                assert resp5.status_code == 200
                msg5 = resp5.json()["message"]
                assert "| 3 |" in msg5
                assert "800 rolls" in msg5

                # Step 6: Remove by keyword: "remove brown tape"
                resp6 = await client.post("/api/chat", json={
                    "message": "remove brown tape",
                    "conversation_id": session_id,
                })
                assert resp6.status_code == 200
                msg6 = resp6.json()["message"]
                assert "| 2 |" in msg6
                assert "| 3 |" not in msg6

    asyncio.run(_test())


def test_freeform_notes_and_email_paragraphs_intake() -> None:
    """Test handling unstructured email paragraphs, conversational greetings, and closing notes:
    - Verifies that greeting boilerplate and unstructured closing remarks/notes are NOT parsed as line items.
    - Verifies that terms embedded in paragraphs (e.g. 45 days credit, delivery to Pune, USD, samples required)
      are mapped to RFI terms metadata.
    """
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-email-paragraphs-notes-session"
                reset_chat_session(session_id)

                email_intake = """
Hi Procurement Team,

Please find our monthly warehouse packaging requirements for Q4 below:

1. 2000 corrugated boxes, 400x300x200 mm, 5 ply kraft paper
2. 500 rolls brown packaging tape 2 inch
3. 300 rolls bubble wrap 50m

Please ensure delivery is completed within 15 days of PO. Payment will be made within 45 days net. All items must be delivered to Pune plant (DDP). All quotes should be in USD and valid for 60 days. Samples required before bulk production.

Thanks and best regards,
Rajesh Sharma
Supply Chain & Operations
"""
                resp = await client.post("/api/chat", json={
                    "message": email_intake,
                    "conversation_id": session_id,
                })
                assert resp.status_code == 200
                msg = resp.json()["message"]

                # Check exactly 3 line items created
                assert "| 1 |" in msg
                assert "| 2 |" in msg
                assert "| 3 |" in msg
                assert "| 4 |" not in msg

                # Verify product details
                assert "Corrugated Boxes" in msg or "corrugated boxes" in msg.lower()
                assert "Brown Packaging Tape" in msg or "brown packaging tape" in msg.lower() or "brown tape" in msg.lower()
                assert "Bubble Wrap" in msg or "bubble wrap" in msg.lower()

                # Verify that commercial terms were extracted from paragraph
                assert "45 Days Net" in msg or "Net 45 Days" in msg or "45 days" in msg.lower()
                assert "USD" in msg
                assert "60 Days" in msg or "60" in msg

                # Now update note/terms conversationally on active RFI
                resp2 = await client.post("/api/chat", json={
                    "message": "Please note: Payment terms updated to 30 days credit and delivery to Bangalore facility",
                    "conversation_id": session_id,
                })
                assert resp2.status_code == 200
                msg2 = resp2.json()["message"]
                assert "30 Days Credit" in msg2 or "30 days" in msg2.lower()
                assert "Bangalore" in msg2

    asyncio.run(_test())





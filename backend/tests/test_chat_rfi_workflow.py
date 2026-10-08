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


def test_natural_language_terms_and_item_modifications() -> None:
    """Test comprehensive natural language modifications for terms, dimensions, materials, and prices:
    - User input: 'payment terms update it to 10 days after delivery'
    - User input: 'update item 1 dimensions to 450x350x250 mm'
    - User input: 'change item 1 material to 7 ply heavy kraft'
    - User input: 'delivery terms update it to Pune plant (DDP)'
    - User input: 'change currency to USD'
    - User input: 'remove payment terms' (resets to default Net 30 Days)
    """
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-nl-terms-and-mods-session"
                reset_chat_session(session_id)

                # Step 1: Create initial RFI
                init_resp = await client.post("/api/chat", json={
                    "message": "1000 corrugated boxes 300x200x150 mm and 500 rolls brown tape",
                    "conversation_id": session_id,
                })
                assert init_resp.status_code == 200
                init_msg = init_resp.json()["message"]
                assert "| 1 |" in init_msg
                assert "| 2 |" in init_msg

                # Step 2: Exact user query: "payment terms update it to 10 days after delivery"
                resp1 = await client.post("/api/chat", json={
                    "message": "payment terms update it to 10 days after delivery",
                    "conversation_id": session_id,
                })
                assert resp1.status_code == 200
                msg1 = resp1.json()["message"]
                assert "10 Days After Delivery" in msg1

                # Step 3: Modify item 1 dimensions: "update item 1 dimensions to 450x350x250 mm"
                resp2 = await client.post("/api/chat", json={
                    "message": "update item 1 dimensions to 450x350x250 mm",
                    "conversation_id": session_id,
                })
                assert resp2.status_code == 200
                msg2 = resp2.json()["message"]
                assert "450 x 350 x 250 mm" in msg2

                # Step 4: Modify item 1 material: "change item 1 material to 7 ply heavy kraft"
                resp3 = await client.post("/api/chat", json={
                    "message": "change item 1 material to 7 ply heavy kraft",
                    "conversation_id": session_id,
                })
                assert resp3.status_code == 200
                msg3 = resp3.json()["message"]
                assert "7 Ply Heavy Kraft" in msg3

                # Step 5: Modify delivery terms: "delivery terms update it to Pune plant (DDP)"
                resp4 = await client.post("/api/chat", json={
                    "message": "delivery terms update it to Pune plant (DDP)",
                    "conversation_id": session_id,
                })
                assert resp4.status_code == 200
                msg4 = resp4.json()["message"]
                assert "Pune Plant (Ddp)" in msg4 or "Pune plant" in msg4.lower()

                # Step 6: Modify currency: "change currency to USD"
                resp5 = await client.post("/api/chat", json={
                    "message": "change currency to USD",
                    "conversation_id": session_id,
                })
                assert resp5.status_code == 200
                msg5 = resp5.json()["message"]
                assert "USD" in msg5

                # Step 7: Reset payment terms: "remove payment terms"
                resp6 = await client.post("/api/chat", json={
                    "message": "remove payment terms",
                    "conversation_id": session_id,
                })
                assert resp6.status_code == 200
                msg6 = resp6.json()["message"]
                assert "Net 30 Days" in msg6

    asyncio.run(_test())


def test_user_transcript_multi_item_modifications_and_phrasings() -> None:
    """Test all specific failure cases from the user transcript:
    1. Multi-item update: 'update quantity of 1 to 350, and 2 to 300 and 3 to 500 with baseline of ₹80'
    2. 'quantity of' phrasing: 'update quantity of 1 to 350'
    3. 'line item' prefix: 'update line item 1 quantity to 300'
    4. Code/bracket phrasing: 'update Pkg-001] 3-Ply Corrugated Box () to 300 pieces'
    5. Negative confirmation with update: 'no, update existing item'
    """
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-transcript-chat-session"
                reset_chat_session(session_id)

                # Step 1: Create 4-item initial RFI
                init_resp = await client.post("/api/chat", json={
                    "message": "433 pcs [Pkg-001] 3-Ply Corrugated Box, 269 pcs [Pkg-002] 5-Ply Heavy Duty Master Box, 113 pcs [Pkg-003] 7-Ply Industrial Shipping Box, and 500m [Pkg-004] Kraft Paper Tape",
                    "conversation_id": session_id,
                })
                assert init_resp.status_code == 200
                init_msg = init_resp.json()["message"]
                assert "| 1 |" in init_msg
                assert "| 2 |" in init_msg
                assert "| 3 |" in init_msg
                assert "| 4 |" in init_msg
                assert "433" in init_msg
                assert "269" in init_msg
                assert "113" in init_msg

                # Step 2: Multi-item update: "update quantity of 1 to 350, and 2 to 300 and 3 to 500 with baseline of ₹80"
                resp1 = await client.post("/api/chat", json={
                    "message": "update quantity of 1 to 350, and 2 to 300 and 3 to 500 with baseline of ₹80",
                    "conversation_id": session_id,
                })
                assert resp1.status_code == 200
                msg1 = resp1.json()["message"]
                # Must NOT trigger duplicate warning or create items 5..7
                assert "Duplicate Item Detected" not in msg1
                assert "| 5 |" not in msg1
                assert "350 pcs" in msg1
                assert "300 pcs" in msg1
                assert "500 pcs" in msg1

                # Step 3: Phrasing: "update quantity of 1 to 350"
                resp2 = await client.post("/api/chat", json={
                    "message": "update quantity of 1 to 350",
                    "conversation_id": session_id,
                })
                assert resp2.status_code == 200
                msg2 = resp2.json()["message"]
                assert "Duplicate Item Detected" not in msg2
                assert "| 5 |" not in msg2
                assert "350 pcs" in msg2

                # Step 4: Phrasing: "update line item 1 quantity to 300"
                resp3 = await client.post("/api/chat", json={
                    "message": "update line item 1 quantity to 300",
                    "conversation_id": session_id,
                })
                assert resp3.status_code == 200
                msg3 = resp3.json()["message"]
                assert "Duplicate Item Detected" not in msg3
                assert "| 5 |" not in msg3
                assert "300 pcs" in msg3

                # Step 5: Phrasing: "update Pkg-001] 3-Ply Corrugated Box () to 300 pieces"
                resp4 = await client.post("/api/chat", json={
                    "message": "update Pkg-001] 3-Ply Corrugated Box () to 300 pieces",
                    "conversation_id": session_id,
                })
                assert resp4.status_code == 200
                msg4 = resp4.json()["message"]
                assert "Duplicate Item Detected" not in msg4
                assert "| 5 |" not in msg4
                assert "300 pieces" in msg4 or "300 pcs" in msg4

                # Step 6: Negative confirmation: "no, update existing item"
                # First create duplicate condition
                dup_resp = await client.post("/api/chat", json={
                    "message": "100 pcs [Pkg-001] 3-Ply Corrugated Box",
                    "conversation_id": session_id,
                })
                assert dup_resp.status_code == 200
                dup_msg = dup_resp.json()["message"]
                assert "Duplicate Item Detected" in dup_msg

                # User says: "no, update existing item"
                no_resp = await client.post("/api/chat", json={
                    "message": "no, update existing item",
                    "conversation_id": session_id,
                })
                assert no_resp.status_code == 200
                no_msg = no_resp.json()["message"]
                # Duplicate must NOT be added
                assert "| 5 |" not in no_msg
                assert "Understood" in no_msg

    asyncio.run(_test())


def test_tabular_key_value_annotated_intake() -> None:
    """Test intake when pasting tabular/key-value annotated data with preambles and commercial terms:
    1. Preambles like 'Bidding Vendors are requested...' and headers '# Description Quantity...' are filtered.
    2. Column values (Category, Target Qty, Baseline) are correctly mapped to semantic fields.
    3. Empty () in descriptions are cleaned.
    4. 2D dimensions (e.g. 48Mm X 50M, 1200X800Mm, 18X24 In) are extracted.
    5. Baseline prices and quantities are properly set.
    6. Commercial terms are extracted without single-letter noise.
    """
    async def _test() -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with app.router.lifespan_context(app):
                app.state.agent_registry.master.model = TestModel()
                for s in app.state.agent_registry.specialists.values():
                    s.model = TestModel()

                session_id = "test-tabular-intake-session"
                reset_chat_session(session_id)

                pasted_text = (
                    "Bidding Vendors are requested to provide itemized rates for the following Packaging Consumables:\n"
                    "#\tDescription\tQuantity\tDimensions\tMaterial / Specs\n"
                    "1\t[Pkg-001] 3-Ply Corrugated Box ()\tCategory: Cartons\tTarget Qty: 433 Piece\tBaseline: ₹19.57\n"
                    "2\t[Pkg-002] 5-Ply Heavy Duty Master Box ()\tCategory: Cartons\tTarget Qty: 269 Piece\tBaseline: ₹46.21\n"
                    "3\t[Pkg-003] 7-Ply Industrial Shipping Box ()\tCategory: Cartons\tTarget Qty: 113 Piece\tBaseline: ₹99.69\n"
                    "4\t[Pkg-004] Kraft Paper Tape (48Mm X 50M)\tCategory: Tape\tTarget Qty: 500 Metre\tBaseline: ₹1.43\n"
                    "5\t[Pkg-005] Bopp Clear Packing Tape 48M (48Mm X 100M)\tCategory: Tape\tTarget Qty: 341 Roll\tBaseline: ₹37.2\n"
                    "6\t[Pkg-006] Cross-Weave Filament Tape (24Mm X 50M)\tCategory: Tape\tTarget Qty: 442 Metre\tBaseline: ₹2.56\n"
                    "7\t[Pkg-007] Lldpe Hand Stretch Wrap Film (23Mic X 500Mm)\tCategory: Film\tTarget Qty:\tBaseline: ₹156.14\n"
                    "8\t[Pkg-008] Machine Grade Stretch Film (500Mm X 1500M)\tCategory: Film\tTarget Qty: 82 Roll\tBaseline: ₹1451.02\n"
                    "9\t[Pkg-009] Air Bubble Wrap Roll (1M X 100M, 10Mm Bubble)\tCategory: Cushioning\tTarget Qty: 463 Metre\tBaseline: ₹6.4\n"
                    "10\t[Pkg-010] Anti-Static Bubble Roll Pink (1M X 50M)\tCategory: Cushioning\tTarget Qty: 301 Metre\tBaseline: ₹18.39\n"
                    "11\t[Pkg-011] Epe Foam Sheet Roll 2Mm (1M X 100M)\tCategory: Cushioning\tTarget Qty: 500 Metre\tBaseline: ₹4.89\n"
                    "12\t[Pkg-012] Kraft Honeycomb Paper Wrap (500Mm X 250M)\tCategory: Cushioning\tTarget Qty: 74 Roll\tBaseline: ₹1165.87\n"
                    "13\t[Pkg-013] Standard Euro Wooden Pallet (1200X800Mm Ht)\tCategory: Pallets\tTarget Qty: 134 Piece\tBaseline: ₹911.99\n"
                    "14\t[Pkg-014] Hdpe Plastic Heavy Duty Pallet (1200X1000Mm)\tCategory: Pallets\tTarget Qty: 60 Piece\tBaseline: ₹2376.07\n"
                    "15\t[Pkg-015] Pet Strapping Roll 15Mm X 0.8Mm X 1000M\tCategory: Strapping\tTarget Qty: 500 Metre\tBaseline: ₹1.92\n"
                    "16\t[Pkg-016] Pp Strapping Roll 12Mm X 2000M (Yellow)\tCategory: Strapping\tTarget Qty: 87 Roll\tBaseline: ₹875.9\n"
                    "17\t[Pkg-017] Heavy Duty Steel Strapping 19Mm X\tCategory: Strapping\tTarget Qty:\tBaseline: ₹127.63\n"
                    "18\t[Pkg-018] Corrugated Edge Protectors (X1000Mm)\tCategory: Protectors\tTarget Qty: 419 Piece\tBaseline: ₹15.44\n"
                    "19\t[Pkg-019] Heavy Duty Plastic Corner Guards (Pack 100)\tCategory: Protectors\tTarget Qty:\tBaseline: ₹228.19\n"
                    "20\t[Pkg-020] Ldpe Transparent Poly Bags 200G (12X16 In)\tCategory: Bags\tTarget Qty:\tBaseline: ₹174.06\n"
                    "21\t[Pkg-021] Zip Lock Reclosable Bags (8X10 In, Pack 500)\tCategory: Bags\tTarget Qty:\tBaseline: ₹443.89\n"
                    "22\t[Pkg-022] Vci Anti-Rust Poly Envelopes (18X24 In)\tCategory: Bags\tTarget Qty: 241 Piece\tBaseline: ₹25.43\n"
                    "23\t[Pkg-023] Thermal Barcode Labels 4X6 In (1000/Roll)\tCategory: Labels\tTarget Qty: 175 Roll\tBaseline: ₹311.02\n"
                    "24\t[Pkg-024] Fragile Advisory Stickers (Roll Of 500)\tCategory: Labels\tTarget Qty: 147 Roll\tBaseline: ₹166.92\n"
                    "25\t[Pkg-025] Silica Gel Desiccant Pouches 50G (Pack 100)\tCategory: Protection\tTarget Qty:\tBaseline: ₹141.17\n"
                    "26\t[Pkg-026] Inflatable Air Cushion Bags (Roll Of 1500)\tCategory: Cushioning\tTarget Qty: 85 Roll\tBaseline: ₹1498.1\n"
                    "27\t[Pkg-027] Corrugated Grid Divider Inserts (12-Cell)\tCategory: Cartons\tTarget Qty: 354 Piece\tBaseline: ₹25.87\n"
                    "28\t[Pkg-028] Self-Adhesive Packing List Envelopes A5\tCategory: Labels\tTarget Qty:\tBaseline: ₹194.61\n"
                    "29\t[Pkg-029] Bubble Lined Kraft Mailers #4 (10X15 In)\tCategory: Bags\tTarget Qty: 480 Piece\tBaseline: ₹15.18\n"
                    "30\t[Pkg-030] Biodegradable Loose Fill Peanuts (10 Cu Ft)\tCategory: Cushioning\tTarget Qty:\tBaseline: ₹791.9\n"
                    "Commercial Terms:\n"
                    "Payment Terms: Net 30 Days\n"
                    "Delivery Terms: Delivered to Bangalore (DDP)\n"
                    "Quote Validity: 30 Days\n"
                    "Currency: INR"
                )

                resp = await client.post("/api/chat", json={
                    "message": pasted_text,
                    "conversation_id": session_id,
                })
                assert resp.status_code == 200
                msg = resp.json()["message"]

                # Preamble must NOT be in Title
                assert "Bidding Vendors are requested" not in msg

                # Check line item parsing and column values
                assert "| 1 | [Pkg-001] 3-Ply Corrugated Box | 433 Piece | Standard | Cartons (Baseline: ₹19.57) |" in msg
                assert "**Payment Terms**: Net 30 Days" in msg
                assert "Delivered to Bangalore (DDP)" in msg

    asyncio.run(_test())






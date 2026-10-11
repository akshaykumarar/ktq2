# Implementation Plan: Decision Analyst Question Answering & Narrative Synthesis Fix

## Problem Summary
When buyers ask procurement analysis questions in the AI Analyst chat (e.g. "Who is cheapest vendor for each item?", "Show quotation summary for all vendors", or newly created free-form user questions):
1. **Placeholder Narrative**: The system executes SQL queries and retrieves rows, but outputs only a placeholder response:
   ```
   ### Analysis for: *{question}*
   Retrieved 30 result records based on verified database queries.
   - Top Record: rfx_item_id: 184 | item_number: 1
   Query Result
   ```
   It fails to synthesize any natural language answer, executive summary, line-item highlights, winner breakdowns, or actionable procurement insights.
2. **Missing / Inflexible Semantic Mapping & Fragile Fallbacks**:
   - Exact-matching against few-shot examples fails if phrasing diverges even slightly (e.g., missing "the", differing punctuation).
   - Several quick questions (e.g., "Check vendor compliance and response status", "Show savings vs target price") lacked exact examples or fell back to generic item dumps.
   - Fallback SQL queries in `SemanticDataEngine` were limited and did not cover general procurement questions (e.g. savings, compliance, lead times, vendor spend).
3. **Raw Table Rendering**:
   - `TableData` exposed raw database primary keys (e.g. `rfx_item_id`, `vendor_id`), unformatted numbers, and raw SQL column identifiers.
   - The UI table had minimal formatting for currency, percentages, or status badges.

## Phased Implementation Plan

### Phase 1: Few-Shot Examples & Semantic Intent Enhancements
- In `config/wren_examples.yaml`:
  - Add explicit, high-quality example pairs for all quick questions:
    - "Who is cheapest vendor for each item?"
    - "Show quotation summary for all vendors"
    - "Check vendor compliance and response status"
    - "Show savings vs target price"
    - "What is the total spend per vendor?"
    - "Which vendor has the shortest lead time?"
    - "Compare vendor prices for line items"
- In `backend/app/analyst/semantic.py`:
  - Normalize queries (remove stop words, punctuation, lowercase) during example retrieval and exact-match checking.
  - Enhance deterministic fallback query builder to cover all major procurement query intents (cheapest, savings, coverage/summary, compliance/quality, lead times, flags/risks, single items).

### Phase 2: Dual-Mode Procurement Narrative Synthesis (LLM + Deterministic Fallback)
- In `backend/app/analyst/orchestrator.py`:
  - Replace the placeholder narrative with an intelligent synthesis engine:
    1. **LLM Synthesis**: Invoke LLM agent with the loaded system prompt (`analyst_system_v1.txt`), the user question, and the structured query results to generate a concise, executive procurement narrative:
       - Direct answer up front.
       - Key findings (e.g., winners by item count, total spend, largest price variances).
       - Quality/state disclosures (CONFIDENT vs REVIEW).
       - Caveats and recommendations.
    2. **Deterministic Fallback Synthesizer**: If the LLM is unavailable (offline, rate limited, or missing keys), execute a comprehensive deterministic narrative synthesizer that analyzes the query rows/columns to generate a detailed, professional breakdown for:
       - Cheapest vendor per line item (item wins per vendor, total spend, average unit price, review flags).
       - Quotation summary & coverage (quoted items, coverage %, response status).
       - Savings vs target price (total savings amount, percentage, items exceeding target).
       - Compliance & knockout status (pass/fail status, certifications, critical flags).
       - General data queries (top highlights, summary statistics).

### Phase 3: Table Data Cleansing & Column Humanization
- In `backend/app/analyst/orchestrator.py`:
  - Transform raw SQL columns into clean, user-friendly labels (e.g., `item_number` -> `Item #`, `rfx_description` -> `Item Description`, `vendor_name` -> `Vendor`, `effective_price_inr` -> `Price (INR)`, `state` -> `Status`).
  - Filter out internal database IDs (`rfx_item_id`, `vendor_id`, `response_id`, `rfx_id`, `id`) from client-facing display tables.
  - In narrative synthesis (both deterministic and LLM prompts), strictly reference items by human-readable Item Number (#) and Item Description/Name, never raw database keys (`rfx_item_id`).
  - Format monetary numbers and percentages cleanly in tables.

### Phase 4: UI Presentation & Quick Asks Alignment
- In `chatbot/analyst.html`:
  - Format currency (`₹`), percentages, and status badges in `<v-table>` dynamically.
  - Ensure quick ask chips match optimized query formulations.
  - Provide empty-state messaging when zero records are returned.

### Phase 5: Verification & Traceability
- Run offline and unit tests.
- Verify existing and new analysis questions.
- Save test logs to `artifacts/logs/`.
- Update documentation: `README.md`, `architecture.md`, `AI_context.md`.

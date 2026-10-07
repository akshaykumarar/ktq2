# Configurable Multi-Agent Procurement Assistant & Chatbot

A configurable multi-agent procurement product powered by **FastAPI** and **PydanticAI**, connected to the existing **AI-QL Chat UI** frontend.

## Architecture

```text
AI-QL Chat UI → FastAPI POST /api/chat → Master Agent → Specialist Agent → Mock Tools
```

- **Master Agent**: Understands user intent and delegates tasks to the appropriate specialist agent.
- **RFX Agent**: Specializes in creating, drafting, and managing RFXs with tools (`create_rfx`, `get_rfx`).
- **Vendor Agent**: Specializes in supplier discovery and vendor evaluations (`search_vendors`, `get_vendor_status`).
- **Status Agent**: Specializes in real-time tracking of RFX progression and vendor responses (`get_rfx_status`, `get_vendor_status`).
- **Preserved Vendor Flow**: Existing vendor webhook evaluation flow remains completely intact and supported.

## Quickstart & Setup

### 1. Prerequisites
- Python 3.10+ (tested with Python 3.14)
- Virtual environment (`.venv`)

### 2. Installation

```bash
# Create virtual environment if not existing
python3 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install fastapi uvicorn "pydantic>=2.10" pyyaml python-dotenv httpx pytest pydantic-ai
```

### 3. Environment Secrets

Copy `.env.example` to `.env` and fill in secrets as needed:

```bash
cp .env.example .env
```

`.env` contains provider secrets:
```dotenv
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
```

> **Note**: If API keys are omitted or offline, the system automatically falls back to safe mock tool execution for instant end-to-end demonstrations without crashing.

### 4. Running the Application

Start the unified FastAPI backend and UI server:

```bash
# From repository root
.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then visit:
- **Chat UI**: [http://localhost:8000/ui/](http://localhost:8000/ui/) (or [http://localhost:8000/](http://localhost:8000/))
- **Interactive API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)

Alternatively, the frontend in `chatbot/` can also be served via any static HTTP server (e.g. `python3 -m http.server 8080`) thanks to CORS enablement.

## Configuration

All models, parameters, and agent system instructions are decoupled from Python code:

### `config/llms.yaml`
Define model presets, temperature, max tokens, and provider:
```yaml
llms:
  reasoning:
    provider: openai
    model: gpt-5
    temperature: 0.1
    max_tokens: 4000

  fast:
    provider: openai
    model: gpt-5-mini
    temperature: 0.2
    max_tokens: 2500
```
Supported providers: `openai`, `anthropic`, `google` (Gemini), `ollama`, and `test`/`mock`.

### `config/agents.yaml`
Map agents to LLM presets and customize their system instructions:
```yaml
agents:
  master:
    llm: reasoning
    instructions: |
      You are the Procurement Assistant.
      Understand the user's request and delegate it
      to the appropriate specialist agent.
      Do not perform specialist work yourself.

  rfx:
    llm: reasoning
    instructions: |
      You are an RFX specialist.
      Help users create and manage procurement RFXs.

  vendor:
    llm: fast
    instructions: |
      You are a Vendor specialist.
      Help users find vendors and check vendor status.

  status:
    llm: fast
    instructions: |
      You are a Procurement Status specialist.
      Help users check RFX and vendor status.
```

Changing an agent's model requires **only editing `config/agents.yaml`** (no code modifications needed).

## API Endpoints

### `POST /api/chat`

**Request:**
```json
{
  "message": "Create an RFX for 200 laptops",
  "conversation_id": "session-123"
}
```

**Response:**
```json
{
  "message": "I have created a new RFX for you:\n\n- **RFX ID**: `RFX-A1B2C3`\n- **Title**: Create an RFX for 200 laptops\n- **Quantity**: 200\n- **Status**: Draft\n...",
  "agent": "rfx",
  "conversation_id": "session-123"
}
```

## Running Tests

Run the full pytest suite:

```bash
.venv/bin/pytest backend/tests -v
```

Test logs are output to [`artifacts/logs/test_run.log`](file:///Users/akshaykumar/code/ktq2/artifacts/logs/test_run.log).

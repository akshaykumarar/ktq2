# RFX Assistant & Procurement Chatbot

An AI-powered assistant for RFX workflows (Request for Proposal / Request for Quotation) and Vendor Response evaluation.

## Features
- **Clean Welcome Interface**: Displays 2 primary workflow options upon launching:
  1. **Create an RFX**: Guided drafting and formulation of new RFX requirements.
  2. **Check Vendor Response**: Automated review, analysis, and comparison of vendor submissions.
- **Config-Driven Architecture**: Chatbot settings (model, API endpoint, headers, parameters) are configured via [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json).
- **Distraction-Free UI**: The text input area is hidden on initial launch until a workflow option is selected, and settings gear icons are removed from the client interface.

## Running Locally

To run the chatbot locally with any static HTTP server:

```bash
# Using Python
cd chatbot
python3 -m http.server 8080

# Or using npx serve
npx serve chatbot
```

Then open `http://localhost:8080` in your browser.

## Configuration & Webhook Integration

The chatbot is configured to interact with a **Make.com scenario** via webhook.

### Webhook Specification

- **Endpoint**: Configured in [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json) (e.g., `https://hook.eu1.make.com/...`).
- **Method**: `POST`
- **Headers**:
  ```http
  Content-Type: application/json
  x-make-apikey: <API_KEY> (or configured via CHATBOTAUTH in .env / apiKey in config.json)
  x-mak-api-key: <API_KEY>
  ```
- **Environment & Config Settings**:
  - `CHATBOTAUTH` in [`.env`](file:///Users/akshaykumar/code/ktq2/.env) or `"apiKey"` in [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json) sets the API key.
  - `CHATBOTURL` in [`.env`](file:///Users/akshaykumar/code/ktq2/.env) or `"url"` in [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json) sets the webhook URL.
- **Request Payload**:
  ```json
  {
    "message": "User message text",
    "conversation_id": "1728283456789"
  }
  ```
  *The `conversation_id` is a timestamp generated on session launch and maintained throughout all turns of that conversation until the user creates a new session or reloads the page.*

- **Webhook Response Format**:
  The Make scenario should return a `200 OK` JSON response containing `reply_message`:
  ```json
  {
    "reply_message": "Agent response text / markdown"
  }
  ```

### Sample `chatbot/config.json`:

```json
{
  "chatbotStore": {
    "url": "https://hook.eu1.make.com/ah791mkhp7r10xwsoljltui2jaymc5xr",
    "apiKey": "aerchain3",
    "authHeader": "x-make-apikey",
    "mode": "webhook",
    "contentType": "application/json"
  }
}
```

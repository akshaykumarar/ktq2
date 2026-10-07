## Current State
- Removed "AI Query Layout" heading, external links, and lottie branding from the initial screen.
- Hid the settings gear icon (`mdi-cog`) and its speed dial from the frontend UI.
- Configured initial state to display 2 interactive action cards ("Create an RFX" and "Check Vendor response") with hidden text input until chosen.
- Configured Make.com webhook into [`chatbot/index.html`](file:///Users/akshaykumar/code/ktq2/chatbot/index.html) and [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json):
  - Sends `{ message, conversation_id }` as JSON payload.
  - Automatically manages `conversation_id` using timestamp per session (persisted throughout the conversation and renewed on "New Conversation" or page refresh).
  - Automatically attaches `x-make-apikey: aerchain3` and `x-mak-api-key: aerchain3` headers.
  - Handles response parsing for `reply_message` (with fallback to `message`, `response`, `content`, or raw text).
  - Configured webhook URL and `apiKey: "aerchain3"` in [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json) with `.env` and default fallback.

## Key Files
- [`chatbot/index.html`](file:///Users/akshaykumar/code/ktq2/chatbot/index.html): Main application template, Vue 3 setup, Vuetify components, Pinia stores, Make.com webhook communication logic with `x-make-apikey` authentication, and workflow dispatchers.
- [`chatbot/config.json`](file:///Users/akshaykumar/code/ktq2/chatbot/config.json): Chatbot settings with Make.com webhook URL, API key (`aerchain3`), and mode.
- [`architecture.md`](file:///Users/akshaykumar/code/ktq2/architecture.md): Sequence diagram and architectural modules.
- [`README.md`](file:///Users/akshaykumar/code/ktq2/README.md): Usage guide and webhook payload/response specifications.

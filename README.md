# Simple LLM Chatbot

This is a minimal Flask-based chatbot that proxies requests to the OpenAI Chat Completions API using your OpenAI API key.

Setup:
1. Create and activate a Python virtual environment:
   python3 -m venv venv
   source venv/bin/activate
2. Install dependencies:
   pip install -r requirements.txt
3. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
4. Run the app:
   python app.py

Open http://localhost:5000 in your browser and try sending messages.

Notes:
- The app reads `OPENAI_API_KEY` from environment or `.env`.
- This is intentionally minimal; feel free to extend conversation state, streaming, or authentication as needed.
 - You can optionally configure Anthropic/Claude by setting `ANTHROPIC_API_KEY` in your `.env`.
 - To choose which provider to use by default, set `DEFAULT_PROVIDER` to `openai` or `anthropic`. Individual requests can override the provider by passing `metadata: {"provider":"anthropic"}`.
 
AgentCost / proxy integration
--------------------------------
If you want to route API calls through AgentCost (or any proxy) to capture cost and metadata, set these environment variables in your `.env`:

- `AGENTCOST_PROXY_URL` — base URL for the proxy (example: `https://proxy.agentcost.dev/v1`). If unset the app will use OpenAI's `https://api.openai.com/v1`.
- `AGENTCOST_HEADERS` — optional JSON object of extra HTTP headers to send with each request. Example:

```text
AGENTCOST_HEADERS={"X-Agent-Name":"my-agent","X-Customer-Id":"cust_123"}
```

With these set the app will send the same OpenAI request through the proxy so you get realtime cost tracking without changing call semantics.

See https://agentcost-production.up.railway.app/ for AgentCost docs and proxy instructions.


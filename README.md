# Simple Chatbot

Flask app that talks to OpenAI, Anthropic, Gemini, and xAI (Grok). Set `OPENAI_API_KEY` in `.env` (required). Optionally set `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and `XAI_API_KEY` to use those providers.

**Run from a fresh terminal:**

```bash
cd /path/to/chatbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:8080**. Copy `.env.example` to `.env` and add your API keys if you haven’t already. If port 8080 is in use, run `PORT=3000 python app.py` (or any free port).

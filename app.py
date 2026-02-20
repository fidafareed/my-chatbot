#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY in environment or .env file")

# Optional: route OpenAI calls through an AgentCost (or other) proxy to capture
# per-call billing/metadata. Set AGENTCOST_PROXY_URL to something like:
#   https://proxy.agentcost.dev/v1
# If unset we default to the official OpenAI API base.
AGENTCOST_PROXY_URL = os.getenv("AGENTCOST_PROXY_URL") or "https://api.openai.com/v1"

# Optional extra headers to send to the proxy / API. Provide a JSON object string:
# AGENTCOST_HEADERS='{"X-Agent-Name":"my-agent","X-Customer-Id":"cust_123"}'
AGENTCOST_HEADERS_RAW = os.getenv("AGENTCOST_HEADERS", "")
AGENTCOST_HEADERS = {}
if AGENTCOST_HEADERS_RAW:
    try:
        AGENTCOST_HEADERS = json.loads(AGENTCOST_HEADERS_RAW)
    except Exception:
        AGENTCOST_HEADERS = {}
# If an AgentCost API key is provided separately, ensure it's included in the
# headers we send to the proxy so both SDK and manual requests include it.
AGENTCOST_API_KEY = os.getenv("AGENTCOST_API_KEY")
if AGENTCOST_API_KEY:
    AGENTCOST_HEADERS.setdefault("X-API-Key", AGENTCOST_API_KEY)

app = Flask(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."
# Anthropic / Claude config (optional)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-2")

USE_OPENAI_SDK = os.getenv("USE_OPENAI_SDK", "1") in ("1", "true", "yes")
OPENAI_SDK_CLIENT = None
if USE_OPENAI_SDK:
    try:
        from openai import OpenAI

        # Build default headers for the SDK (AgentCost may require its own API key header)
        sdk_default_headers = AGENTCOST_HEADERS.copy()
        # If the proxy expects an AgentCost API key header, support AGENTCOST_API_KEY env var
        AGENTCOST_API_KEY = os.getenv("AGENTCOST_API_KEY")
        if AGENTCOST_API_KEY:
            sdk_default_headers.setdefault("X-API-Key", AGENTCOST_API_KEY)

        OPENAI_SDK_CLIENT = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=AGENTCOST_PROXY_URL.rstrip("/"),
            default_headers=sdk_default_headers or None,
        )
    except Exception:
        OPENAI_SDK_CLIENT = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    metadata = data.get("metadata", {}) or {}
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    # merge any optional AgentCost / proxy headers
    headers.update(AGENTCOST_HEADERS)

    # merge per-request metadata into X- headers (agent_name -> X-Agent-Name)
    for k, v in metadata.items():
        if v is None:
            continue
        header_name = "X-" + "-".join(part.capitalize() for part in k.split("_"))
        headers[header_name] = str(v)

    # Decide provider: allow per-request override via metadata["provider"]
    provider = (metadata.get("provider") or os.getenv("DEFAULT_PROVIDER", "openai") or "openai").lower()

    # build endpoint (if AGENTCOST_PROXY_URL already includes /v1 it's fine to append)
    endpoint = AGENTCOST_PROXY_URL.rstrip("/") + "/chat/completions"

    # Debug logging (prints to Flask console)
    try:
        safe_headers = {hk: ("REDACTED" if hk.lower() == "authorization" else hv) for hk, hv in headers.items()}
        print("[LLM proxy] endpoint:", endpoint)
        print("[LLM proxy] headers:", safe_headers)
        print("[LLM proxy] payload:", json.dumps(payload))
    except Exception:
        pass

    try:
        # If Anthropic/Claude is requested and configured, call their API
        if provider in ("anthropic", "claude"):
            if not ANTHROPIC_API_KEY:
                return jsonify({"error": "Anthropic provider requested but ANTHROPIC_API_KEY not set"}), 400

            # Build a simple prompt from system + user for Claude-style models
            anthropic_prompt = f"{SYSTEM_PROMPT}\n\nHuman: {user_message}\n\nAssistant:"
            anthropic_base = os.getenv("ANTHROPIC_PROXY_URL") or ANTHROPIC_BASE_URL
            anthropic_endpoint = anthropic_base.rstrip("/") + "/v1/complete"
            # Anthropic/Claude expects the API key in the `x-api-key` header.
            # Keep a Bearer-style Authorization header as well for proxies that accept it.
            anthropic_headers = {
                "x-api-key": ANTHROPIC_API_KEY,
                "Authorization": f"Bearer {ANTHROPIC_API_KEY}",
                "Content-Type": "application/json",
            }
            # merge agentcost/proxy headers if provided
            anthropic_headers.update(AGENTCOST_HEADERS)

            anthropic_payload = {
                "model": metadata.get("anthropic_model") or ANTHROPIC_MODEL,
                "prompt": anthropic_prompt,
                "max_tokens_to_sample": payload.get("max_tokens", 500),
                "temperature": payload.get("temperature", 0.7),
            }

            resp = requests.post(anthropic_endpoint, json=anthropic_payload, headers=anthropic_headers, timeout=30)
        else:
            # OpenAI path (existing behavior). Use SDK when available and no per-request metadata.
            if metadata or OPENAI_SDK_CLIENT is None:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            else:
                # Use OpenAI SDK client when available for nicer integration with proxy
                # The SDK returns a mapping-like object; convert to dict
                sdk_resp = OPENAI_SDK_CLIENT.chat.completions.create(
                    model=payload["model"],
                    messages=payload["messages"],
                    temperature=payload.get("temperature"),
                    max_tokens=payload.get("max_tokens"),
                )
                # Try to extract content directly from SDK response (handles object or dict)
                content = None
                try:
                    # dict-like access
                    if isinstance(sdk_resp, dict):
                        content = sdk_resp["choices"][0]["message"]["content"]
                    else:
                        # object-style access (ChatCompletion)
                        first = getattr(sdk_resp, "choices", None)
                        if first:
                            first_item = first[0]
                            msg = getattr(first_item, "message", None) or getattr(first_item, "text", None)
                            if msg is not None:
                                # message may be object with content attribute or a dict
                                if hasattr(msg, "content"):
                                    content = getattr(msg, "content")
                                elif isinstance(msg, dict):
                                    content = msg.get("content")
                                else:
                                    # fallback: string conversion
                                    content = str(msg)
                except Exception:
                    content = None

                if content is not None:
                    return jsonify({"reply": content})
                # fallback: serialize sdk_resp to string
                return jsonify({"reply": str(sdk_resp)})
    except Exception as e:
        print("[LLM proxy] request exception:", str(e))
        return jsonify({"error": "Request exception", "details": str(e)}), 500

    # Log response status and body for debugging
    try:
        resp_text = resp.text
    except Exception:
        resp_text = "<unreadable response body>"
    print(f"[LLM proxy] response status: {resp.status_code}")
    print(f"[LLM proxy] response body: {resp_text}")

    # Attempt to parse JSON reply
    try:
        j = resp.json()
    except Exception:
        return jsonify({"error": "Upstream did not return JSON", "status": resp.status_code, "details": resp_text}), 502

    # Attempt to extract text from multiple provider response shapes.
    def _extract_content(resp_json):
        # OpenAI Chat Completions (choices -> message -> content)
        try:
            return resp_json["choices"][0]["message"]["content"]
        except Exception:
            pass

        # OpenAI older/text completion (choices -> text)
        try:
            return resp_json["choices"][0]["text"]
        except Exception:
            pass

        # OpenAI "Responses" API style: output -> [ { content: [ { text: "..." } ] } ]
        try:
            out = resp_json.get("output") or resp_json.get("outputs")
            if isinstance(out, list) and out:
                first = out[0]
                # content may be nested
                if isinstance(first, dict):
                    # content.text
                    c = first.get("content") or first.get("content_type") or first.get("data")
                    if isinstance(c, list) and c:
                        # try common nested shapes
                        maybe_text = c[0].get("text") if isinstance(c[0], dict) else None
                        if maybe_text:
                            return maybe_text
                    # fallback to first.get("text")
                    if first.get("text"):
                        return first.get("text")
        except Exception:
            pass

        # Anthropic / Claude older endpoint: "completion"
        if isinstance(resp_json, dict) and "completion" in resp_json:
            return resp_json.get("completion")

        # Some Anthropic proxies return {"id":..., "model":..., "output": "text"} or {"text": "..."}
        if isinstance(resp_json, dict) and "output" in resp_json and isinstance(resp_json["output"], str):
            return resp_json["output"]
        if isinstance(resp_json, dict) and "text" in resp_json:
            return resp_json.get("text")

        # Last resort: try to stringify top-level 'message' or first string value
        if isinstance(resp_json, dict):
            if "message" in resp_json and isinstance(resp_json["message"], str):
                return resp_json["message"]
            # scan for first simple string value
            for v in resp_json.values():
                if isinstance(v, str) and len(v) > 0:
                    return v

        return None

    content = _extract_content(j)
    if content is None:
        # return full JSON for easier debugging
        return jsonify({"error": "Unexpected response shape", "status": resp.status_code, "body": j}), 502

    return jsonify({"reply": content})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))


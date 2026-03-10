#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

# Create a requests session that ignores system proxy settings.
# This avoids corporate/OS proxies that may block Railway/Spendline.
session = requests.Session()
session.trust_env = False
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY in environment or .env file")

# Optional: route OpenAI calls through Spendline (or AgentCost) proxy to capture
# per-call billing/metadata. Set SPENDLINE_URL, or AGENTCOST_URL / AGENTCOST_PROXY_URL.
# Example: https://agentcost-production.up.railway.app
# If unset we default to the official OpenAI API base.
AGENTCOST_URL_RAW = os.getenv("SPENDLINE_URL") or os.getenv("AGENTCOST_URL") or os.getenv("AGENTCOST_PROXY_URL") or ""
if AGENTCOST_URL_RAW:
    AGENTCOST_PROXY_URL = AGENTCOST_URL_RAW.rstrip("/") + "/v1" if not AGENTCOST_URL_RAW.endswith("/v1") else AGENTCOST_URL_RAW
else:
    AGENTCOST_PROXY_URL = "https://api.openai.com/v1"

# Optional extra headers to send to the proxy / API. Provide a JSON object string:
AGENTCOST_HEADERS='{"X-Agent-Name":"my-agent","X-Customer-Id":"fida"}'
AGENTCOST_HEADERS_RAW = os.getenv("AGENTCOST_HEADERS", "")
AGENTCOST_HEADERS = {}
if AGENTCOST_HEADERS_RAW:
    try:
        AGENTCOST_HEADERS = json.loads(AGENTCOST_HEADERS_RAW)
    except Exception:
        AGENTCOST_HEADERS = {}
# If a Spendline (or AgentCost) API key is provided, add it to proxy headers.
AGENTCOST_API_KEY = os.getenv("SPENDLINE_API_KEY") or os.getenv("AGENTCOST_API_KEY")
if AGENTCOST_API_KEY:
    AGENTCOST_HEADERS.setdefault("X-API-Key", AGENTCOST_API_KEY)

# Agent and customer IDs for request tracking
AGENT_ID = os.getenv("AGENT_ID", "my-chatbot")
CUSTOMER_ID = os.getenv("CUSTOMER_ID", "fida")

app = Flask(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."
# Provider configs (all routed through AgentCost)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-beta")

USE_OPENAI_SDK = os.getenv("USE_OPENAI_SDK", "1") in ("1", "true", "yes")
OPENAI_SDK_CLIENT = None
if USE_OPENAI_SDK:
    try:
        from openai import OpenAI

        # Build default headers for the SDK (AgentCost may require its own API key header)
        sdk_default_headers = AGENTCOST_HEADERS.copy()
        # Proxy API key (SPENDLINE_API_KEY or AGENTCOST_API_KEY)
        _proxy_key = os.getenv("SPENDLINE_API_KEY") or os.getenv("AGENTCOST_API_KEY")
        if _proxy_key:
            sdk_default_headers.setdefault("X-API-Key", _proxy_key)

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
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "X-API-Key": AGENTCOST_API_KEY,
        "x-agent-id": AGENT_ID,
        "x-customer-id": CUSTOMER_ID,
    }

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
        # Route all providers through AgentCost proxy
        if provider in ("anthropic", "claude"):
            if not ANTHROPIC_API_KEY:
                return jsonify({"error": "Anthropic provider requested but ANTHROPIC_API_KEY not set"}), 400

            # Anthropic-native path; avoid header collision: Spendline key in x-agentcost-key, Anthropic key in x-api-key
            anthropic_payload = {
                "model": metadata.get("anthropic_model") or ANTHROPIC_MODEL,
                "max_tokens": payload.get("max_tokens", 500),
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            }
            anthropic_headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": ANTHROPIC_API_KEY,
                "x-agent-id": AGENT_ID,
                "x-customer-id": CUSTOMER_ID,
            }
            if AGENTCOST_API_KEY:
                anthropic_headers["x-agentcost-key"] = AGENTCOST_API_KEY
            endpoint_url = AGENTCOST_PROXY_URL.rstrip("/") + "/messages"
            resp = session.post(endpoint_url, json=anthropic_payload, headers=anthropic_headers, timeout=30)
            
        elif provider == "gemini":
            if not GEMINI_API_KEY:
                return jsonify({"error": "Gemini provider requested but GEMINI_API_KEY not set"}), 400

            # Same as curl: proxy infers provider from model name (e.g. gemini-2.0-flash)
            gemini_payload = {
                "model": metadata.get("gemini_model") or GEMINI_MODEL,
                "messages": payload["messages"],
                "temperature": payload.get("temperature", 0.7),
                "max_tokens": payload.get("max_tokens", 500),
            }
            gemini_headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GEMINI_API_KEY}",
                "x-agent-id": AGENT_ID,
                "x-customer-id": CUSTOMER_ID,
            }
            gemini_headers.update(AGENTCOST_HEADERS)
            endpoint_url = AGENTCOST_PROXY_URL.rstrip("/") + "/chat/completions"
            resp = session.post(endpoint_url, json=gemini_payload, headers=gemini_headers, timeout=30)
            
        elif provider == "xai":
            if not XAI_API_KEY:
                return jsonify({"error": "xAI provider requested but XAI_API_KEY not set"}), 400
            
            # Same as curl: proxy infers provider from model name (e.g. grok-4-1-fast-reasoning)
            xai_payload = {
                "model": metadata.get("xai_model") or XAI_MODEL,
                "messages": payload["messages"],
                "temperature": payload.get("temperature", 0.7),
                "max_tokens": payload.get("max_tokens", 500),
            }
            xai_headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {XAI_API_KEY}",
                "x-agent-id": AGENT_ID,
                "x-customer-id": CUSTOMER_ID,
            }
            xai_headers.update(AGENTCOST_HEADERS)
            
            endpoint_url = AGENTCOST_PROXY_URL.rstrip("/") + "/chat/completions"
            resp = session.post(endpoint_url, json=xai_payload, headers=xai_headers, timeout=30)
            
        else:
            # Default to OpenAI. Use SDK when available and no per-request metadata.
            if metadata or OPENAI_SDK_CLIENT is None:
                resp = session.post(endpoint, json=payload, headers=headers, timeout=30)
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

    # Surface proxy/auth errors (401, 403, 5xx) so the user sees the real message
    if resp.status_code >= 400:
        err_msg = None
        if isinstance(j.get("error"), dict):
            err_msg = j["error"].get("message") or j["error"].get("code") or str(j["error"])
        elif isinstance(j.get("error"), str):
            err_msg = j["error"]
        if not err_msg:
            err_msg = resp_text[:500] if resp_text else f"Upstream returned {resp.status_code}"
        return jsonify({"error": err_msg, "status": resp.status_code}), resp.status_code if resp.status_code < 500 else 502

    # Attempt to extract text from multiple provider response shapes.
    def _extract_content(resp_json):
        # Anthropic Messages API: content -> [ { type, text } ]
        try:
            blocks = resp_json.get("content") or []
            if isinstance(blocks, list) and blocks:
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "text" and "text" in b:
                        return b["text"]
        except Exception:
            pass

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


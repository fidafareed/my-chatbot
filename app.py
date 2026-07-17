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

# Spendline: OpenAI-compatible proxy. Provider key stays in Authorization (or
# x-api-key for Anthropic). Spendline tenant auth is x-spendline-key only — never
# put the Spendline key in Authorization.
SPENDLINE_API_KEY = os.getenv("SPENDLINE_API_KEY") or os.getenv("AGENTCOST_API_KEY")
SPENDLINE_URL_RAW = os.getenv("SPENDLINE_URL") or os.getenv("AGENTCOST_URL") or os.getenv("AGENTCOST_PROXY_URL") or ""
if SPENDLINE_URL_RAW:
    SPENDLINE_BASE_URL = SPENDLINE_URL_RAW.rstrip("/")
    if not SPENDLINE_BASE_URL.endswith("/v1"):
        SPENDLINE_BASE_URL += "/v1"
elif SPENDLINE_API_KEY:
    SPENDLINE_BASE_URL = "https://www.spendline.ai/v1"
else:
    SPENDLINE_BASE_URL = "https://api.openai.com/v1"

if SPENDLINE_URL_RAW and not SPENDLINE_API_KEY:
    raise RuntimeError("SPENDLINE_URL is set but SPENDLINE_API_KEY is missing in .env")

# Optional extra proxy headers (JSON object string).
SPENDLINE_EXTRA_HEADERS = {}
_extra_headers_raw = os.getenv("SPENDLINE_HEADERS") or os.getenv("AGENTCOST_HEADERS") or ""
if _extra_headers_raw:
    try:
        SPENDLINE_EXTRA_HEADERS = json.loads(_extra_headers_raw)
    except Exception:
        SPENDLINE_EXTRA_HEADERS = {}

AGENT_ID = os.getenv("AGENT_ID", "support-bot-v2")
CUSTOMER_ID = os.getenv("CUSTOMER_ID", "acme-corp")
COST_CENTER = os.getenv("COST_CENTER", "engineering")
SPENDLINE_TAGS = json.dumps({"cost_center": COST_CENTER})


def spendline_headers(provider_headers):
    """Provider auth in provider_headers; Spendline tenant auth via x-spendline-key."""
    headers = dict(provider_headers)
    if SPENDLINE_API_KEY:
        headers["x-spendline-key"] = SPENDLINE_API_KEY
    headers["x-agent-id"] = AGENT_ID
    headers["x-customer-id"] = CUSTOMER_ID
    headers["x-spendline-tags"] = SPENDLINE_TAGS
    headers.update(SPENDLINE_EXTRA_HEADERS)
    return headers


def apply_metadata_headers(headers, metadata):
    for k, v in (metadata or {}).items():
        if v is None:
            continue
        header_name = "X-" + "-".join(part.capitalize() for part in k.split("_"))
        headers[header_name] = str(v)
    return headers


def log_spendline_response(resp):
    logged = resp.headers.get("x-spendline-logged")
    print(f"[LLM proxy] x-spendline-logged: {logged}")
    if SPENDLINE_API_KEY and logged != "true":
        print("[LLM proxy] WARNING: Spendline did not confirm this call was logged to your dashboard")

app = Flask(__name__)

SYSTEM_PROMPT = "You are a helpful assistant."
# Provider configs (all routed through AgentCost)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.20-reasoning")

USE_OPENAI_SDK = os.getenv("USE_OPENAI_SDK", "1") in ("1", "true", "yes")
OPENAI_SDK_CLIENT = None
if USE_OPENAI_SDK:
    try:
        from openai import OpenAI

        sdk_default_headers = spendline_headers({})

        OPENAI_SDK_CLIENT = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=SPENDLINE_BASE_URL,
            default_headers=sdk_default_headers,
        )
    except Exception:
        OPENAI_SDK_CLIENT = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/spendline/verify")
def spendline_verify():
    """Confirm Spendline is receiving and recording calls for this API key."""
    if not SPENDLINE_API_KEY:
        return jsonify({"ok": False, "error": "SPENDLINE_API_KEY is not set"}), 400

    try:
        verify_headers = spendline_headers({"Content-Type": "application/json"})
        calls_resp = session.get(
            SPENDLINE_BASE_URL.rstrip("/").removesuffix("/v1") + "/api/calls?limit=3",
            headers=verify_headers,
            timeout=15,
        )
        calls_resp.raise_for_status()
        calls = calls_resp.json().get("calls", [])
        return jsonify({
            "ok": True,
            "proxy_url": SPENDLINE_BASE_URL,
            "agent_id": AGENT_ID,
            "customer_id": CUSTOMER_ID,
            "recent_calls": len(calls),
            "latest_call": calls[0] if calls else None,
            "dashboard_url": "https://www.spendline.ai/dashboard",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    user_message = data.get("message", "").strip()
    metadata = data.get("metadata", {}) or {}
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    payload = {
        "model": metadata.get("openai_model") or os.getenv("OPENAI_MODEL", "gpt-4.1"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    headers = apply_metadata_headers(spendline_headers({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }), metadata)

    # Decide provider: allow per-request override via metadata["provider"]
    provider = (metadata.get("provider") or os.getenv("DEFAULT_PROVIDER", "openai") or "openai").lower()

    endpoint = SPENDLINE_BASE_URL.rstrip("/") + "/chat/completions"

    # Debug logging (prints to Flask console)
    try:
        _redact = {"authorization", "x-api-key", "x-spendline-key"}
        safe_headers = {hk: ("REDACTED" if hk.lower() in _redact else hv) for hk, hv in headers.items()}
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

            # Anthropic-native path; Spendline key in x-spendline-key, Anthropic key in x-api-key
            anthropic_payload = {
                "model": metadata.get("anthropic_model") or ANTHROPIC_MODEL,
                "max_tokens": payload.get("max_tokens", 500),
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            }
            anthropic_headers = apply_metadata_headers(spendline_headers({
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": ANTHROPIC_API_KEY,
            }), metadata)

            endpoint_url = SPENDLINE_BASE_URL.rstrip("/") + "/messages"
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
            gemini_headers = apply_metadata_headers(spendline_headers({
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GEMINI_API_KEY}",
            }), metadata)
            endpoint_url = SPENDLINE_BASE_URL.rstrip("/") + "/chat/completions"
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
            xai_headers = apply_metadata_headers(spendline_headers({
                "Content-Type": "application/json",
                "Authorization": f"Bearer {XAI_API_KEY}",
            }), metadata)

            endpoint_url = SPENDLINE_BASE_URL.rstrip("/") + "/chat/completions"
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
    log_spendline_response(resp)
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
    if SPENDLINE_API_KEY:
        print(f"[Spendline] proxy={SPENDLINE_BASE_URL} agent={AGENT_ID} customer={CUSTOMER_ID}")
    else:
        print("[Spendline] WARNING: SPENDLINE_API_KEY not set — calls will not appear in dashboard")
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))


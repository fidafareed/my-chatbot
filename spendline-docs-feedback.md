# Spendline setup instructions – feedback and improvement asks

Below are specific asks to improve the setup manual so customers get the best experience and fewer integration issues. Each section states the **ask**, **why it matters**, and **suggested wording or structure** where helpful.

---

## 1. Unify Spendline auth header naming

**Ask:** Pick one canonical header for Spendline authentication and document it everywhere. If both `X-API-Key` and `x-spendline-key` are required for different flows, say so explicitly in one place and reference it from every section.

**Why:** Right now Universal, Python, Raw HTTP, and Verify It Works use `X-API-Key`, while the Anthropic SDK section uses `x-spendline-key` with a note about avoiding `x-api-key` collision. Customers don’t know which header to use for which integration.

**Suggested approach:**
- Add a short “Spendline auth header” callout at the top (or in a “Conventions” section):
  - **OpenAI-compatible requests (Python, Node, raw fetch/cURL):** use `X-API-Key` for your Spendline key; provider key goes in `Authorization: Bearer <key>`.
  - **Anthropic SDK:** Anthropic uses `x-api-key` for the Claude key (set via SDK `apiKey`). Use `X-API-Key` (or `x-spendline-key` if that’s what your proxy expects) for the Spendline key in `defaultHeaders` to avoid collision.
- In the Optional Tracking Headers table, list the Spendline auth header once with a “Required” badge and point to this convention.

---

## 2. Clarify base_url / baseURL and path (avoid double /v1)

**Ask:** For every SDK (Python OpenAI, Node Anthropic, etc.), state explicitly what `base_url` / `baseURL` should be and whether the SDK adds `/v1` itself.

**Why:** If the SDK already appends `/v1` (e.g. `/v1/chat/completions`) and docs say `base_url = '.../v1'`, users can end up with `.../v1/v1/chat/completions` and 404s. This is a common cause of “it doesn’t work” with no obvious clue.

**Suggested approach:**
- For each SDK section, add one line after the code sample, e.g.:
  - “Set `base_url` to `https://agentcost-production.up.railway.app` (no trailing `/v1`) because the OpenAI client appends `/v1`.”
  - Or: “Set `baseURL` to `https://agentcost-production.up.railway.app/v1` because this SDK does not add `/v1`.”
- Optionally add a small table: SDK | base_url value | Reason.

---

## 3. Verify It Works – add Anthropic and fix hosted vs self-hosted

**Ask:**  
(a) Extend “Verify It Works” so it can confirm the Anthropic path.  
(b) Separate “Hosted” vs “Self-hosted” in the verify script and troubleshooting text.

**Why:** The current verify script only checks `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `MISTRAL_API_KEY` and calls `/v1/chat/completions`. Customers using only the Anthropic SDK never get a green check. Also, the error message “Is the Spendline server running? (node server/index.js)” suggests self-hosting while the main docs use the hosted Railway URL, which confuses hosted-only users.

**Suggested approach:**
- **Anthropic verify:** Add a second small script (e.g. “Verify with Anthropic”) that:
  - Reads `ANTHROPIC_API_KEY` from env.
  - Uses `@anthropic-ai/sdk` with the same `baseURL` and Spendline headers you document for Claude.
  - Calls `anthropic.messages.create(...)` once and prints success or the error.
- **Hosted vs self-hosted:**
  - In “Verify It Works”, add a line: “Using the hosted proxy? You don’t need to run any server—just ensure your `.env` has a provider key and Spendline key.”
  - Move “Is the Spendline server running? (node server/index.js)” into a separate “Self-hosted proxy” troubleshooting subsection so it’s clear that’s only for people running their own Spendline instance.

---

## 4. Security and key-handling wording

**Ask:** Replace “Spendline doesn’t store or see your Anthropic key — it just forwards it” with accurate language.

**Why:** A proxy must receive the key to forward the request, so “doesn’t see” is incorrect and can undermine trust when technical readers notice. What you want to convey is that you don’t persist or log provider keys.

**Suggested wording:**  
“Spendline does not store or log your provider API keys; they are used only in memory to forward the request to your provider and are not retained.”

---

## 5. Routing and mental model (one place)

**Ask:** Add one short “How Spendline routes your request” (or “How it works”) section that explains how the proxy decides which provider to call (e.g. by endpoint, model name, or key type). Reference it from the compatibility line (“OpenAI, Anthropic, Gemini, xAI, Mistral”).

**Why:** Customers need to know they can keep their usual model names and that Spendline will route correctly. One clear explanation avoids repeated “which key / which endpoint for which provider?” questions.

**Suggested approach:**
- A small box or short paragraph at the top:  
  “You keep your existing provider key and send requests to Spendline’s URL with Spendline headers. Spendline logs cost and metadata, then forwards the request to [OpenAI / Anthropic / Gemini / xAI / Mistral] based on [e.g. the endpoint and model you use]. The response you get is the same as calling the provider directly.”
- If routing rules differ by provider (e.g. model prefix, or specific path), list them in one place (table or bullets).

---

## 6. Required vs recommended vs optional headers

**Ask:** In the Optional Tracking Headers table (or equivalent), label each header as **Required**, **Recommended**, or **Optional**, and keep that consistent with the rest of the docs.

**Why:** Right now the section is titled “Optional Tracking Headers” but the table lists `X-API-Key` as “Required,” which is confusing. Customers need to know the minimum required set (e.g. Spendline auth + maybe `x-agent-id`) vs nice-to-haves (`x-customer-id`, `x-workflow-id`, etc.).

**Suggested approach:**
- Rename the section to something like “Headers” or “Spendline headers and optional tracking.”
- In the table, add a “Required?” column: Required | Recommended | Optional.
- In the intro sentence, state: “Only the Spendline auth header is required; the rest are optional but recommended for better cost attribution.”

---

## 7. Use env vars in all code samples

**Ask:** In every code sample, use environment variables for the Spendline URL and Spendline API key (e.g. `process.env.SPENDLINE_URL`, `process.env.SPENDLINE_API_KEY`, or `os.environ["SPENDLINE_API_KEY"]`) instead of hardcoding the Railway URL and key.

**Why:** Reduces copy-paste of secrets into code, matches the “put keys in .env” message, and makes it obvious how to switch between staging/prod or self-hosted vs hosted.

**Suggested approach:**
- In Step 2 (or the shared env section), define `SPENDLINE_URL` and `SPENDLINE_API_KEY` once.
- In every snippet that currently shows the raw URL/key, replace with the env var and a one-line comment: “From your .env.”

---

## 8. Common issues / troubleshooting panel

**Ask:** Add a “Common issues” or “Troubleshooting” section with short, actionable fixes for the errors users are most likely to see.

**Why:** One place for “401”, “404”, “timeout”, “wrong model” etc. speeds up support and reduces frustration.

**Suggested content (adapt to your real behavior):**
- **401 / 403:** Check that `X-API-Key` (or `x-spendline-key`) is your Spendline key and that `Authorization` (or Anthropic `apiKey`) is your provider key; keys not swapped or missing.
- **404 / “route not found”:** Usually `base_url` or path—confirm whether your SDK adds `/v1` and adjust `base_url` so you don’t get double `/v1`.
- **Timeout or 5xx:** Check provider status and your provider key; if the request reaches Spendline, include request ID or timestamp when contacting support.
- **Empty or wrong response:** Confirm the `model` name is correct for the provider you’re using.

---

## 9. “What success looks like” for Verify It Works

**Ask:** Under the Verify It Works script, add a short “What success looks like” (example console output or response snippet) and “If it fails” with the top 2–3 checks (e.g. env keys set, correct Spendline URL, no double `/v1`).

**Why:** Users need to know they’re done and what to do next when the script doesn’t print success.

**Suggested approach:**
- After the script: “Success: you should see a short model reply and the message ‘Spendline is working! Open your dashboard to see this call.’”
- “If it fails: (1) Confirm one provider key and your Spendline key are in `.env`. (2) Confirm the request URL is exactly … (no double /v1). (3) For Anthropic, use the Anthropic verify script instead.”

---

## 10. Optional: quick “stack” chooser and production checklist

**Ask (optional but high impact):** At the top, add a “How are you connecting?” chooser (e.g. Python | Node/OpenAI | Node/Anthropic | Raw HTTP) that jumps to the right section. Optionally add a short “Production checklist” (e.g. use env vars, set `x-agent-id`, don’t commit `.env`, check dashboard after first call).

**Why:** Reduces scrolling and wrong-section confusion. A checklist sets expectations for going live and reinforces best practices.

---

## Summary table (for your internal prioritization)

| # | Ask | Impact |
|---|-----|--------|
| 1 | Unify Spendline auth header naming | High – removes header confusion |
| 2 | Clarify base_url / baseURL and /v1 | High – prevents 404s |
| 3 | Verify It Works: Anthropic + hosted vs self-hosted | High – completes verification story |
| 4 | Security/key-handling wording | Medium – trust and accuracy |
| 5 | Routing / mental model in one place | Medium – fewer “how does it work?” questions |
| 6 | Required vs recommended vs optional headers | Medium – clearer table and section title |
| 7 | Env vars in all code samples | Medium – security and consistency |
| 8 | Common issues / troubleshooting | High – better self-serve support |
| 9 | “What success looks like” for verify script | Medium – faster time-to-confidence |
| 10 | Stack chooser + production checklist (optional) | Nice-to-have – best UX |

---

You can share this document as-is with the Spendline team or copy the sections you want into an email/ticket. If you’d like, I can turn a subset into a shorter “top 5” or “must-fix” list.

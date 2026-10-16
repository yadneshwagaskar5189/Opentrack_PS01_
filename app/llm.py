"""Minimal client for any OpenAI-compatible chat API (stdlib only, no SDK needed).

Works with Groq, Google Gemini (OpenAI-compatible endpoint), OpenRouter,
OpenCode, Together, Ollama and OpenAI itself. Configure in .env:
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
"""
import json
import re
import urllib.error
import urllib.request

from . import config


class LLMError(Exception):
    pass


def chat(messages: list[dict], json_mode: bool = False, temperature: float = 0.1) -> str:
    if config.LLM_PROVIDER == "mock":
        raise LLMError("chat() is not available in mock mode")
    if not config.LLM_API_KEY and "localhost" not in config.LLM_BASE_URL:
        raise LLMError("LLM_API_KEY is not set. Add it to .env, or set LLM_PROVIDER=mock for offline demo mode.")
    body = {"model": config.LLM_MODEL, "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        return _post(body)
    except LLMError as e:
        # Some providers/models don't support JSON mode - retry once without it
        msg = str(e).lower()
        if json_mode and ("response_format" in msg or "error 400" in msg):
            body.pop("response_format", None)
            return _post(body)
        raise


def _post(body: dict) -> str:
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise LLMError(f"LLM API error {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMError(f"Could not reach the LLM API at {url}: {e}") from e
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected LLM response: {str(data)[:300]}") from e


def parse_json(text: str) -> dict:
    """Extract the first JSON object from a model reply (handles ```json fences and chatter)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"Model did not return JSON: {text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Model returned invalid JSON: {e}") from e

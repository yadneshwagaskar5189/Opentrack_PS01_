"""Minimal client for any OpenAI-compatible chat API (stdlib only, no SDK needed).

Works with Groq, Google Gemini (OpenAI-compatible endpoint), OpenRouter,
OpenCode, Together, Ollama and OpenAI itself. Configure in .env:
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
"""
import json
import re
import time
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
    if "gpt-oss" in config.LLM_MODEL and config.LLM_REASONING_EFFORT:
        body["reasoning_effort"] = config.LLM_REASONING_EFFORT
    try:
        return _post_retrying(body)
    except LLMError as e:
        # Some providers/models don't support JSON mode or reasoning_effort - retry once without them
        msg = str(e).lower()
        if "error 400" in msg and ("reasoning" in msg or "response_format" in msg or json_mode):
            body.pop("reasoning_effort", None)
            if "reasoning" not in msg:
                body.pop("response_format", None)
            return _post_retrying(body)
        raise


class RateLimited(LLMError):
    def __init__(self, message: str, wait_s: float | None):
        super().__init__(message)
        self.wait_s = wait_s


def _wait_seconds(headers, detail: str) -> float | None:
    """How long the provider asks us to wait: Retry-After header, or 'try again in 1m2.5s' in the message."""
    try:
        if headers and headers.get("retry-after"):
            return float(headers.get("retry-after"))
    except ValueError:
        pass
    m = re.search(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?\s*(?:([\d.]+)ms)?", detail)
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0) + float(ms or 0) / 1000


def _post_retrying(body: dict) -> str:
    """Free tiers allow few tokens per minute: when told to slow down, wait (briefly) and try again."""
    waited = 0.0
    for attempt in range(4):
        try:
            return _post(body)
        except RateLimited as e:
            wait = e.wait_s if e.wait_s is not None else 5.0 * (attempt + 1)
            if wait > config.LLM_RETRY_MAX_WAIT_S or waited + wait > 2 * config.LLM_RETRY_MAX_WAIT_S or attempt == 3:
                limit = "daily" if wait > 120 else "per-minute"
                raise LLMError(f"The AI provider's {limit} usage limit is reached (it asks to wait about "
                               f"{_human(wait)}). Try again then, use another API key, or set LLM_PROVIDER=mock "
                               f"for offline demo mode.") from e
            time.sleep(wait + 0.5)
            waited += wait + 0.5
    raise LLMError("The AI provider is busy. Please try again.")


def _human(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds} s"


def _post(body: dict) -> str:
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "User-Agent": "institute-copilot/1.0",   # Cloudflare blocks the default Python-urllib agent
    })
    try:
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429:
            raise RateLimited(f"LLM API error 429: {detail}", _wait_seconds(e.headers, detail)) from e
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

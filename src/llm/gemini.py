"""Gemini via its native REST API (generateContent) with function calling.

Why native REST and not an SDK or the OpenAI-compatible endpoint: Google AI Studio now issues
API keys starting with "AQ.", which work when sent in the x-goog-api-key header to the native
endpoint, but have been reported to fail on the OpenAI-compatible endpoint. Native REST also
lets us send back Gemini's "thought signatures" exactly as received, which newer models require
during multi-turn function calling.
"""
import json
import random
import re
import sys
import time

import requests

from src.config import (GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL, LLM_MAX_RETRIES,
                        LLM_REQUESTS_PER_MINUTE, LLM_TIMEOUT_SECONDS)
from src.llm.base import (ChatSession, LLMError, LLMResponse, QuotaExhausted, RateLimiter,
                          ToolCall, ToolSpec)

_limiter = RateLimiter(LLM_REQUESTS_PER_MINUTE)


def _say(msg: str) -> None:
    """Progress messages go to stderr so you can see why the program is waiting."""
    print(f"    [gemini] {msg}", file=sys.stderr, flush=True)
_TYPE_MAP = {"object": "OBJECT", "string": "STRING", "integer": "INTEGER", "number": "NUMBER",
             "boolean": "BOOLEAN", "array": "ARRAY"}


def _to_gemini_schema(schema: dict) -> dict:
    """JSON schema -> Gemini Schema (uppercase type names)."""
    out = {}
    for k, v in schema.items():
        if k == "type":
            out["type"] = _TYPE_MAP.get(v, v)
        elif k == "properties":
            out["properties"] = {name: _to_gemini_schema(p) for name, p in v.items()}
        elif k == "items":
            out["items"] = _to_gemini_schema(v)
        elif k in ("description", "required", "enum"):
            out[k] = v
    return out


def _retry_delay(err_json: dict, attempt: int) -> float:
    for d in (err_json.get("error", {}).get("details") or []):
        if "retryDelay" in d:
            m = re.match(r"([\d.]+)s", str(d["retryDelay"]))
            if m:
                return float(m.group(1)) + 1.0
    return min(60.0, 2 ** attempt) + random.random()


def _is_daily_quota(err_json: dict) -> bool:
    text = json.dumps(err_json)
    return "PerDay" in text or "per day" in text.lower()


class GeminiSession(ChatSession):
    def __init__(self, system: str, tools: list[ToolSpec] | None = None, model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None, temperature: float = 0.0):
        self.model = model or GEMINI_MODEL
        self.api_key = api_key or GEMINI_API_KEY
        self.base_url = (base_url or GEMINI_BASE_URL).rstrip("/")
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set. Put it in .env (see .env.example).")
        self.contents: list[dict] = []
        self.body_base = {
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"temperature": temperature},
        }
        if tools:
            self.body_base["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description,
                 "parameters": _to_gemini_schema(t.parameters)} for t in tools]}]

    # ------------------------------------------------------------------ http
    def _post(self, body: dict) -> dict:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        last = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            _limiter.wait()
            try:
                r = requests.post(url, headers=headers, json=body, timeout=LLM_TIMEOUT_SECONDS)
            except requests.RequestException as e:
                last = f"network error: {e}"
                wait = min(30, 2 ** attempt)
                _say(f"network problem ({type(e).__name__}), retrying in {wait}s "
                     f"(attempt {attempt + 1}/{LLM_MAX_RETRIES + 1})")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            try:
                err = r.json()
            except ValueError:
                err = {"error": {"message": r.text[:300]}}
            msg = err.get("error", {}).get("message", r.text[:300])
            if r.status_code == 429:
                if _is_daily_quota(err):
                    raise QuotaExhausted(f"Gemini daily quota reached for {self.model}: {msg}")
                wait = _retry_delay(err, attempt)
                _say(f"rate limited by Google (429), waiting {wait:.0f}s then retrying "
                     f"(attempt {attempt + 1}/{LLM_MAX_RETRIES + 1}). Message: {msg[:160]}")
                time.sleep(wait)
                last = f"429 rate limited: {msg}"
                continue
            if r.status_code in (500, 502, 503, 504):
                wait = min(60, 2 ** attempt) + random.random()
                _say(f"Google server busy ({r.status_code}), retrying in {wait:.0f}s "
                     f"(attempt {attempt + 1}/{LLM_MAX_RETRIES + 1})")
                time.sleep(wait)
                last = f"{r.status_code}: {msg}"
                continue
            if r.status_code in (401, 403):
                raise LLMError(f"Gemini rejected the API key ({r.status_code}): {msg}")
            if r.status_code == 404:
                raise LLMError(f"Model '{self.model}' not found ({msg}). Run: python -m scripts.check_llm "
                               "to list the models your key can use, then set GEMINI_MODEL in .env.")
            raise LLMError(f"Gemini error {r.status_code}: {msg}")
        raise LLMError(f"Gemini request failed after {LLM_MAX_RETRIES + 1} attempts ({last}).")

    # ------------------------------------------------------------------ chat
    def send(self, user_text=None, tool_results=None) -> LLMResponse:
        if tool_results:
            parts = []
            for call, result in tool_results:
                fr = {"name": call.name, "response": {"result": result}}
                if call.id:
                    fr["id"] = call.id
                parts.append({"functionResponse": fr})
            self.contents.append({"role": "user", "parts": parts})
        if user_text is not None:
            self.contents.append({"role": "user", "parts": [{"text": user_text}]})

        t0 = time.time()
        data = self._post({**self.body_base, "contents": self.contents})
        seconds = time.time() - t0

        cands = data.get("candidates") or []
        usage = data.get("usageMetadata", {})
        if not cands:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason}).")
        cand = cands[0]
        content = cand.get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        if not content.get("parts"):
            # e.g. finishReason MALFORMED_FUNCTION_CALL: an empty turn would make the next request
            # invalid, so record a placeholder and let the agent ask the model to continue.
            content["parts"] = [{"text": f"(empty response: {cand.get('finishReason', 'unknown')})"}]
        # Keep the model turn EXACTLY as returned (including thoughtSignature fields).
        self.contents.append(content)

        text, calls = [], []
        for p in content["parts"]:
            if "functionCall" in p:
                fc = p["functionCall"]
                calls.append(ToolCall(name=fc.get("name", ""), args=fc.get("args", {}) or {},
                                      id=fc.get("id")))
            elif "text" in p and not p.get("thought") and not p["text"].startswith("(empty response"):
                text.append(p["text"])
        return LLMResponse(
            text="".join(text).strip(), tool_calls=calls,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0)),
            seconds=seconds, finish_reason=cand.get("finishReason", ""))


def list_models(api_key: str | None = None, base_url: str | None = None) -> list[dict]:
    """Models this key can call with generateContent."""
    key = api_key or GEMINI_API_KEY
    url = f"{(base_url or GEMINI_BASE_URL).rstrip('/')}/models?pageSize=200"
    r = requests.get(url, headers={"x-goog-api-key": key}, timeout=30)
    if r.status_code != 200:
        raise LLMError(f"Could not list models ({r.status_code}): {r.text[:300]}")
    models = r.json().get("models", [])
    return [m for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]

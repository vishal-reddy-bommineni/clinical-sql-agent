"""Any OpenAI-compatible chat API (Groq, OpenAI, OpenRouter, Ollama, Anthropic's compatibility
endpoint, ...). Set LLM_PROVIDER=openai_compatible plus the OPENAI_COMPAT_* settings in .env."""
import json
import random
import time

import requests

from src.config import (LLM_MAX_RETRIES, LLM_REQUESTS_PER_MINUTE, LLM_TIMEOUT_SECONDS,
                        OPENAI_COMPAT_API_KEY, OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_MODEL)
from src.llm.base import (ChatSession, LLMError, LLMResponse, QuotaExhausted, RateLimiter,
                          ToolCall, ToolSpec)

_limiter = RateLimiter(LLM_REQUESTS_PER_MINUTE)


class OpenAICompatSession(ChatSession):
    def __init__(self, system: str, tools: list[ToolSpec] | None = None, model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None, temperature: float = 0.0):
        self.model = model or OPENAI_COMPAT_MODEL
        self.api_key = api_key or OPENAI_COMPAT_API_KEY
        self.base_url = (base_url or OPENAI_COMPAT_BASE_URL).rstrip("/")
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in (tools or [])]
        self.temperature = temperature

    def _post(self, body):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            _limiter.wait()
            try:
                r = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=body,
                                  timeout=LLM_TIMEOUT_SECONDS)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code == 200:
                return r.json()
            last = f"{r.status_code}: {r.text[:300]}"
            if r.status_code == 429:
                if "per day" in r.text.lower() or "daily" in r.text.lower():
                    raise QuotaExhausted(last)
                wait = float(r.headers.get("retry-after", 0) or 0) or min(60, 2 ** attempt)
                time.sleep(wait + random.random())
                continue
            if r.status_code >= 500:
                time.sleep(min(60, 2 ** attempt))
                continue
            raise LLMError(f"API error {last}")
        raise LLMError(f"Request failed after retries ({last}).")

    def send(self, user_text=None, tool_results=None) -> LLMResponse:
        for call, result in tool_results or []:
            self.messages.append({"role": "tool", "tool_call_id": call.id or call.name,
                                  "content": result})
        if user_text is not None:
            self.messages.append({"role": "user", "content": user_text})
        body = {"model": self.model, "messages": self.messages, "temperature": self.temperature}
        if self.tools:
            body["tools"] = self.tools
        t0 = time.time()
        data = self._post(body)
        msg = data["choices"][0]["message"]
        self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc["function"].get("arguments")}
            calls.append(ToolCall(name=tc["function"]["name"], args=args, id=tc.get("id")))
        usage = data.get("usage", {})
        return LLMResponse(text=(msg.get("content") or "").strip(), tool_calls=calls,
                           input_tokens=usage.get("prompt_tokens", 0),
                           output_tokens=usage.get("completion_tokens", 0),
                           seconds=time.time() - t0,
                           finish_reason=data["choices"][0].get("finish_reason", ""))

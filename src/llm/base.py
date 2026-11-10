"""Provider-neutral types for chatting with an LLM that can call tools.

Each provider keeps its own conversation history in its native format (Gemini needs its
'thought signatures' sent back exactly as received), so the agent only deals with these types.
"""
import threading
import time
from dataclasses import dataclass, field


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON schema: {"type": "object", "properties": {...}, "required": [...]}


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str | None = None


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    finish_reason: str = ""


class LLMError(RuntimeError):
    """Any provider error the agent cannot recover from (bad key, bad model, blocked)."""


class QuotaExhausted(LLMError):
    """Daily / hard quota reached. The evaluation stops and can be resumed later."""


class ChatSession:
    """One conversation. send() takes either a user message or tool results."""
    model: str = ""

    def send(self, user_text: str | None = None,
             tool_results: list[tuple[ToolCall, str]] | None = None) -> LLMResponse:
        raise NotImplementedError


class RateLimiter:
    """Spaces out requests so free-tier per-minute limits are respected (shared by all sessions)."""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)

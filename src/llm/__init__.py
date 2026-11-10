"""make_session() returns a chat session for the provider set in .env (LLM_PROVIDER)."""
from src.config import GEMINI_MODEL, LLM_PROVIDER, OPENAI_COMPAT_MODEL
from src.llm.base import ChatSession, LLMError, LLMResponse, QuotaExhausted, ToolCall, ToolSpec


def make_session(system: str, tools=None, provider: str | None = None, **kw) -> ChatSession:
    provider = (provider or LLM_PROVIDER).lower()
    if provider == "gemini":
        from src.llm.gemini import GeminiSession
        return GeminiSession(system, tools, **kw)
    if provider in ("openai_compatible", "openai", "groq", "ollama"):
        from src.llm.openai_compat import OpenAICompatSession
        return OpenAICompatSession(system, tools, **kw)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini' or 'openai_compatible'.")


def current_model_name(provider: str | None = None) -> str:
    provider = (provider or LLM_PROVIDER).lower()
    return GEMINI_MODEL if provider == "gemini" else OPENAI_COMPAT_MODEL


__all__ = ["make_session", "current_model_name", "ChatSession", "LLMError", "LLMResponse",
           "QuotaExhausted", "ToolCall", "ToolSpec"]

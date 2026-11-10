"""All settings in one place. Values come from environment variables or a local .env file
(never commit .env: it holds your API key). See .env.example."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

RAW_DIR = ROOT / "data" / "raw" / "mimic-iv-demo-2.2"
DB_PATH = ROOT / "data" / "mimic_demo.duckdb"
CATALOG_PATH = ROOT / "src" / "catalog.yaml"
QUESTIONS_PATH = ROOT / "src" / "evaluation" / "questions.yaml"
REPORTS_DIR = ROOT / "reports"
RUNS_DIR = REPORTS_DIR / "runs"
FIG_DIR = REPORTS_DIR / "figures"


def _secret(name: str, default: str = "") -> str:
    """Environment variable first; on Streamlit Cloud, fall back to st.secrets."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        return str(st.secrets.get(name, default))
    except Exception:
        return default


# ----------------------------------------------------------------------------- LLM
# LLM_PROVIDER: "gemini" (native Gemini REST API, default) or "openai_compatible"
# (Groq, OpenAI, Ollama, OpenRouter, Anthropic's OpenAI-compatible endpoint, ...)
LLM_PROVIDER = _secret("LLM_PROVIDER", "gemini")
GEMINI_API_KEY = _secret("GEMINI_API_KEY")
GEMINI_MODEL = _secret("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_BASE_URL = _secret("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

OPENAI_COMPAT_BASE_URL = _secret("OPENAI_COMPAT_BASE_URL", "https://api.groq.com/openai/v1")
OPENAI_COMPAT_API_KEY = _secret("OPENAI_COMPAT_API_KEY")
OPENAI_COMPAT_MODEL = _secret("OPENAI_COMPAT_MODEL", "llama-3.3-70b-versatile")

# Free tiers allow only a few requests per minute. The client waits between calls.
LLM_REQUESTS_PER_MINUTE = float(_secret("LLM_REQUESTS_PER_MINUTE", "8"))
LLM_TIMEOUT_SECONDS = float(_secret("LLM_TIMEOUT_SECONDS", "90"))
LLM_MAX_RETRIES = int(_secret("LLM_MAX_RETRIES", "6"))

# ----------------------------------------------------------------------------- SQL safety
MAX_RESULT_ROWS = 500          # rows returned to the user / app
MAX_ROWS_SHOWN_TO_LLM = 20     # rows the agent sees after running a query
QUERY_TIMEOUT_SECONDS = 20

# ----------------------------------------------------------------------------- agent
AGENT_MAX_STEPS = 8            # max LLM turns per question

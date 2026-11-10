import os
import sys
from pathlib import Path

# Tests never call a real API and never wait for rate limits.
os.environ["LLM_REQUESTS_PER_MINUTE"] = "0"
os.environ["LLM_MAX_RETRIES"] = "3"
os.environ.setdefault("GEMINI_API_KEY", "AQ.test-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from src.build_db import ensure_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    ensure_db()


@pytest.fixture()
def mock_gemini():
    import mock_gemini as mg
    server, state, url = mg.start()
    yield state, url
    server.shutdown()

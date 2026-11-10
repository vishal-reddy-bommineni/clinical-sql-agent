"""End-to-end tests of the Gemini client, agent loop and evaluation against the mock server."""
import functools

import pytest
import yaml

from src.agent.runner import run_agent, run_baseline
from src.config import QUESTIONS_PATH
from src.db import run_query
from src.evaluation.compare import results_match
from src.llm import LLMError, QuotaExhausted
from src.llm.gemini import GeminiSession, list_models

QUESTIONS = yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]


def factory(url, key="AQ.test-key", model="gemini-flash-latest"):
    return functools.partial(GeminiSession, api_key=key, base_url=url, model=model)


def test_baseline_roundtrip(mock_gemini):
    state, url = mock_gemini
    q = QUESTIONS[0]
    state.answers[q["question"]] = q["gold_sql"]
    res = run_baseline(q["question"], with_catalog=True, session_factory=factory(url))
    assert res.error is None and res.llm_calls == 1
    assert results_match(run_query(q["gold_sql"]).df, res.df)
    assert res.input_tokens > 0 and res.output_tokens == 70   # thoughts tokens counted
    assert not state.errors


def test_agent_tool_loop_keeps_thought_signatures(mock_gemini):
    state, url = mock_gemini
    q = next(x for x in QUESTIONS if x["id"] == "q45")
    state.answers[q["question"]] = q["gold_sql"]
    res = run_agent(q["question"], session_factory=factory(url))
    assert res.error is None, res.error
    assert results_match(run_query(q["gold_sql"]).df, res.df)
    assert res.llm_calls == 3 and res.tool_calls == 3
    assert [s["type"] for s in res.steps][-1] == "final"
    assert not state.errors, state.errors
    # tool declarations were converted to Gemini schema and sent
    assert state.requests[0]["body"]["tools"][0]["functionDeclarations"][0]["parameters"]["type"] == "OBJECT"


def test_agent_self_corrects_after_sql_error(mock_gemini):
    state, url = mock_gemini
    q = next(x for x in QUESTIONS if x["id"] == "q23")
    state.answers[q["question"]] = q["gold_sql"]
    state.first_sql[q["question"]] = "SELECT long_title FROM diagnoses_icd"   # wrong: column not there
    res = run_agent(q["question"], session_factory=factory(url))
    assert res.error is None
    runs = [s for s in res.steps if s.get("name") == "run_sql"]
    assert runs[0]["ok"] is False and runs[1]["ok"] is True
    assert results_match(run_query(q["gold_sql"]).df, res.df, order_matters=True)
    assert not state.errors, state.errors


def test_retries_on_429_and_503(mock_gemini):
    state, url = mock_gemini
    state.fail_plan = [429, 503]
    s = GeminiSession("system", api_key="AQ.test-key", base_url=url)
    r = s.send(user_text="hello")
    assert "```sql" in r.text
    assert len(state.requests) == 3


def test_daily_quota_raises(mock_gemini):
    state, url = mock_gemini
    state.daily_quota = True
    with pytest.raises(QuotaExhausted):
        run_agent("anything", session_factory=factory(url))


def test_bad_key_and_bad_model_give_clear_errors(mock_gemini):
    state, url = mock_gemini
    with pytest.raises(LLMError, match="rejected the API key"):
        GeminiSession("s", api_key="AQ.wrong", base_url=url).send(user_text="hi")
    with pytest.raises(LLMError, match="check_llm"):
        GeminiSession("s", api_key="AQ.test-key", base_url=url, model="unknown-model").send(user_text="hi")
    # and the agent turns it into a recorded error instead of crashing
    res = run_agent("hi", session_factory=factory(url, key="AQ.wrong"))
    assert res.error.startswith("LLM error")


def test_list_models(mock_gemini):
    state, url = mock_gemini
    names = [m["name"] for m in list_models(api_key="AQ.test-key", base_url=url)]
    assert names == ["models/gemini-flash-latest"]


def test_oracle_scores_100_percent_on_all_questions(mock_gemini):
    """If the 'model' returns the gold SQL, every question must score correct: proves the
    harness, the guard and the comparison accept every gold answer through the agent path."""
    state, url = mock_gemini
    for q in QUESTIONS:
        state.answers[q["question"]] = q["gold_sql"]
    for q in QUESTIONS:
        res = run_agent(q["question"], session_factory=factory(url))
        assert res.error is None, (q["id"], res.error)
        assert results_match(run_query(q["gold_sql"]).df, res.df, bool(q.get("order_matters"))), q["id"]
    assert not state.errors, state.errors

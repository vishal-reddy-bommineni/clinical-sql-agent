"""Three ways to answer a question, compared in the evaluation:

  baseline_schema   one LLM call; prompt has only the raw schema (CREATE TABLE statements)
  baseline_catalog  one LLM call; prompt has the full documented catalog (descriptions + gotchas)
  agent             tool-using loop: explore tables, look up values, run and fix SQL, then answer
"""
import re
import time
from dataclasses import asdict, dataclass, field

from src import catalog
from src.agent import prompts, tools
from src.config import AGENT_MAX_STEPS
from src.db import run_query
from src.llm import LLMError, QuotaExhausted, make_session

CONFIGS = ("baseline_schema", "baseline_catalog", "agent")


@dataclass
class RunResult:
    config: str
    question: str
    sql: str = ""
    answer: str = ""
    error: str | None = None          # SQL error or LLM failure, if any
    llm_calls: int = 0
    tool_calls: int = 0
    sql_attempts: int = 0             # how many queries the agent ran
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    steps: list = field(default_factory=list)   # trace for the app and for error analysis
    df: object = None                 # final result (pandas DataFrame), not serialized

    def to_dict(self):
        d = asdict(self)
        d.pop("df", None)
        return d


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql|duckdb)?\s*(.*?)```", text or "", re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(WITH|SELECT)\b.*", text or "", re.S | re.I)
    return m.group(0).strip() if m else ""


def _account(res: RunResult, resp):
    res.llm_calls += 1
    res.input_tokens += resp.input_tokens
    res.output_tokens += resp.output_tokens


def run_baseline(question: str, with_catalog: bool, session_factory=None) -> RunResult:
    session_factory = session_factory or make_session
    cfg = "baseline_catalog" if with_catalog else "baseline_schema"
    res = RunResult(config=cfg, question=question)
    t0 = time.time()
    schema_text = (catalog.notes_text() + "\n\n" + catalog.full_catalog_text()) if with_catalog else catalog.ddl_text()
    system = prompts.BASELINE_SYSTEM.format(rules=prompts.SQL_RULES, schema=schema_text)
    try:
        session = session_factory(system, None)
        resp = session.send(user_text=question)
        _account(res, resp)
        res.sql = extract_sql(resp.text)
        res.steps.append({"type": "llm", "text": resp.text[:4000]})
        if not res.sql:
            res.error = "No SQL in model response."
        else:
            q = run_query(res.sql)
            res.sql_attempts = 1
            res.df, res.error = q.df, q.error
            res.sql = q.sql or res.sql
    except QuotaExhausted:
        raise
    except LLMError as e:
        res.error = f"LLM error: {e}"
    res.seconds = time.time() - t0
    return res


def run_agent(question: str, session_factory=None, max_steps: int = AGENT_MAX_STEPS,
              on_step=None) -> RunResult:
    """on_step(step_dict) is called after every step (the app uses it to show progress)."""
    session_factory = session_factory or make_session
    res = RunResult(config="agent", question=question)
    t0 = time.time()
    system = prompts.AGENT_SYSTEM.format(rules=prompts.SQL_RULES, notes=catalog.notes_text(),
                                         overview=catalog.overview_text())
    last_ok_sql, last_ok_df = None, None

    def log(step):
        res.steps.append(step)
        if on_step:
            on_step(step)

    try:
        session = session_factory(system, tools.TOOLS)
        resp = session.send(user_text=question)
        _account(res, resp)
        nudged = False
        for _ in range(max_steps):
            if resp.text:
                log({"type": "thought", "text": resp.text[:2000]})
            if not resp.tool_calls:
                sql = extract_sql(resp.text)
                if sql and not nudged:
                    # model answered in text: accept the SQL it wrote
                    q = run_query(sql)
                    res.sql_attempts += 1
                    if q.ok:
                        res.sql, res.df, res.answer = q.sql, q.df, resp.text
                        break
                if nudged:
                    break
                nudged = True
                resp = session.send(user_text=prompts.AGENT_NUDGE)
                _account(res, resp)
                continue

            results, finished = [], False
            for call in resp.tool_calls:
                res.tool_calls += 1
                a = call.args or {}
                if call.name == "describe_table":
                    out = tools.describe_table(a.get("table", ""))
                    log({"type": "tool", "name": call.name, "args": a, "output": out[:1500]})
                elif call.name == "search_values":
                    out = tools.search_values(a.get("table", ""), a.get("column", ""), a.get("contains", ""))
                    log({"type": "tool", "name": call.name, "args": a, "output": out[:1500]})
                elif call.name == "run_sql":
                    out, q = tools.run_sql_tool(a.get("sql", ""))
                    res.sql_attempts += 1
                    if q.ok:
                        last_ok_sql, last_ok_df = q.sql, q.df
                    log({"type": "tool", "name": call.name, "args": a, "output": out[:1500],
                         "ok": q.ok})
                elif call.name == "final_answer":
                    q = run_query(a.get("sql", ""))
                    res.sql_attempts += 1
                    res.sql, res.df, res.answer = q.sql, q.df, str(a.get("answer", ""))
                    res.error = q.error
                    out = "Recorded." if q.ok else f"Error: {q.error}. Fix the SQL and call final_answer again."
                    log({"type": "final", "args": a, "ok": q.ok, "output": out})
                    finished = q.ok
                else:
                    out = f"Unknown tool '{call.name}'."
                    log({"type": "tool", "name": call.name, "args": a, "output": out})
                results.append((call, out))
            if finished:
                break
            resp = session.send(tool_results=results)
            _account(res, resp)
        else:
            res.error = res.error or f"Stopped after {max_steps} steps without a final answer."

        if res.df is None and last_ok_sql:
            # fall back to the last query that ran successfully
            res.sql, res.df, res.error = last_ok_sql, last_ok_df, None
            res.answer = res.answer or "(no final answer; showing the last successful query)"
            log({"type": "fallback", "sql": last_ok_sql})
        elif res.df is None and not res.error:
            res.error = "No query produced."
    except QuotaExhausted:
        raise
    except LLMError as e:
        res.error = f"LLM error: {e}"
    res.seconds = time.time() - t0
    return res


def answer(question: str, config: str = "agent", **kw) -> RunResult:
    if config == "agent":
        return run_agent(question, **kw)
    if config == "baseline_catalog":
        return run_baseline(question, with_catalog=True, **kw)
    if config == "baseline_schema":
        return run_baseline(question, with_catalog=False, **kw)
    raise ValueError(f"Unknown config {config}")

"""SQL safety, answer comparison, answer key and catalog."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest
import yaml

from src import catalog
from src.agent import tools
from src.config import QUESTIONS_PATH
from src.db import UnsafeSQL, check_sql, run_query
from src.evaluation.compare import results_match
from src.llm.openai_compat import OpenAICompatSession

QUESTIONS = yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE patients", "DELETE FROM patients", "UPDATE patients SET gender='X'",
    "INSERT INTO patients VALUES (1)", "SELECT 1; DROP TABLE patients", "COPY patients TO 'x.csv'",
    "ATTACH 'other.db'", "PRAGMA database_list", "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM '/etc/passwd'", "CREATE TABLE x AS SELECT 1", "SET threads=1", "SELEC 1"])
def test_guard_blocks(sql):
    with pytest.raises(UnsafeSQL):
        check_sql(sql)
    assert not run_query(sql).ok


@pytest.mark.parametrize("sql", ["SELECT 1", "WITH a AS (SELECT 1 x) SELECT * FROM a",
                                 "SELECT 1 UNION ALL SELECT 2", "```sql\nSELECT 1;\n```"])
def test_guard_allows(sql):
    assert run_query(sql).ok


def test_timeout():
    r = run_query("SELECT COUNT(*) FROM chartevents a, chartevents b", timeout=1)
    assert "timed out" in r.error


def test_row_cap():
    r = run_query("SELECT * FROM chartevents", max_rows=10)
    assert len(r.df) == 10 and r.truncated


def test_every_gold_query_runs_and_question_ids_unique():
    assert len(QUESTIONS) == 50
    assert len({q["id"] for q in QUESTIONS}) == 50
    for q in QUESTIONS:
        r = run_query(q["gold_sql"])
        assert r.ok and len(r.df) > 0, q["id"]


def test_key_gold_answers():
    ans = {q["id"]: run_query(q["gold_sql"]).df for q in QUESTIONS}
    assert ans["q01"].iloc[0, 0] == 100
    assert ans["q02"].iloc[0, 0] == 275
    assert ans["q08"].iloc[0, 0] == 140


@pytest.mark.parametrize("gold,pred,ordered,expected", [
    ({"a": [100]}, {"n": [100]}, False, True),
    ({"a": [100]}, {"n": [101]}, False, False),
    ({"a": [6.88]}, {"x": [6.8812]}, False, True),
    ({"a": [43.3]}, {"x": [0.433]}, False, False),
    ({"g": ["F", "M"], "n": [43, 57]}, {"cnt": [57, 43], "sex": ["M", "F"]}, False, True),
    ({"g": ["F", "M"], "n": [43, 57]}, {"g": ["F", "M"], "n": [57, 43]}, False, False),
    ({"g": ["a", "b"], "n": [2, 1]}, {"g": ["b", "a"], "n": [1, 2]}, True, False),
    ({"a": [1]}, {"label": ["x"], "a": [1]}, False, True),
])
def test_compare(gold, pred, ordered, expected):
    assert results_match(pd.DataFrame(gold), pd.DataFrame(pred), ordered) is expected


def test_catalog_covers_every_table():
    cat = catalog.load_catalog()["tables"]
    assert set(catalog.table_names()) == set(cat)
    for t in catalog.table_names():
        for col in (cat[t].get("columns") or {}):
            assert col in {c for c, _ in catalog.schema()[t]["columns"]}, (t, col)


def test_search_values_tool():
    out = tools.search_values("d_labitems", "label", "creatinine")
    assert "50912" in out
    assert "Unknown column" in tools.search_values("d_labitems", "label; DROP", "x")


def test_openai_compatible_client_roundtrip():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.headers.get("Authorization"), body))
            if len(body["messages"]) == 2:
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function",
                       "function": {"name": "run_sql", "arguments": json.dumps({"sql": "SELECT 1"})}}]}
            else:
                assert body["messages"][-1]["role"] == "tool" and body["messages"][-1]["tool_call_id"] == "t1"
                msg = {"role": "assistant", "content": "done"}
            out = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = OpenAICompatSession("sys", tools.TOOLS, model="m", api_key="k",
                                base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1")
        r1 = s.send(user_text="q")
        assert r1.tool_calls[0].name == "run_sql" and r1.tool_calls[0].args == {"sql": "SELECT 1"}
        r2 = s.send(tool_results=[(r1.tool_calls[0], "1 row")])
        assert r2.text == "done" and seen[0][0] == "Bearer k"
    finally:
        srv.shutdown()


def test_clean_sql_drops_language_line():
    # run 1 found a model reply "```\nsql\nSELECT ...```" that was wrongly rejected
    assert run_query("sql\nSELECT MEDIAN(los) AS m FROM icustays").ok
    assert run_query("```sql\nSELECT 1;\n```").ok


def test_profiling_queries_run():
    from scripts.profile_data import SQL_PATH
    chunks = SQL_PATH.read_text().split("-- name:")[1:]
    assert len(chunks) >= 10
    for chunk in chunks:
        title, query = chunk.split("\n", 1)
        r = run_query(query)
        assert r.ok and len(r.df) > 0, title

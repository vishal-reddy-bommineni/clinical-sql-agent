"""A local HTTP server that imitates the Gemini native REST API (generateContent), used only by
the tests. It checks that our client speaks the protocol correctly:
  - API key sent in the x-goog-api-key header
  - the model's previous turns sent back unchanged, INCLUDING thoughtSignature fields
  - every functionResponse answers the functionCall before it (same name and id)
and it can simulate rate limits (429 with retryDelay), server errors (503) and daily quota.

Its "model" follows a script: describe a table, run SQL, then give a final answer, using the
SQL the test supplies for each question. It is a test double, not a language model.
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "AQ.test-key"


class MockState:
    def __init__(self):
        self.answers = {}            # question text -> SQL the fake model should use
        self.first_sql = {}          # question text -> a broken SQL to try first (tests self-correction)
        self.fail_plan = []          # list of status codes to return before succeeding (429, 503, ...)
        self.daily_quota = False
        self.requests = []
        self.errors = []             # protocol violations found


def make_handler(state: MockState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("x-goog-api-key") != API_KEY:
                return self._send(401, {"error": {"code": 401, "message": "API key not valid."}})
            self._send(200, {"models": [
                {"name": "models/gemini-flash-latest", "displayName": "Gemini Flash Latest",
                 "supportedGenerationMethods": ["generateContent", "countTokens"]},
                {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append({"path": self.path, "body": body})
            if self.headers.get("x-goog-api-key") != API_KEY:
                return self._send(401, {"error": {"code": 401, "message": "API key not valid.",
                                                  "status": "UNAUTHENTICATED"}})
            if self.headers.get("Authorization"):
                state.errors.append("Authorization header must not be sent with x-goog-api-key")
            if "unknown-model" in self.path:
                return self._send(404, {"error": {"code": 404, "message": "models/unknown-model is not found"}})
            if state.daily_quota:
                return self._send(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exceeded", "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
            if state.fail_plan:
                code = state.fail_plan.pop(0)
                details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0s"}] if code == 429 else []
                return self._send(code, {"error": {"code": code, "message": "simulated", "details": details}})
            self._check_protocol(body)
            self._send(200, self._reply(body))

        # ------------------------------------------------------------ protocol checks
        def _check_protocol(self, body):
            contents = body.get("contents", [])
            if not contents or contents[0]["role"] != "user":
                state.errors.append("conversation must start with a user turn")
            for i, c in enumerate(contents):
                if c["role"] == "model":
                    for p in c["parts"]:
                        if "functionCall" in p and "thoughtSignature" not in p:
                            state.errors.append(f"turn {i}: thoughtSignature was dropped")
                    calls = [p["functionCall"] for p in c["parts"] if "functionCall" in p]
                    if calls and i + 1 < len(contents):
                        nxt = contents[i + 1]
                        resps = [p["functionResponse"] for p in nxt["parts"] if "functionResponse" in p]
                        if [(x["name"], x.get("id")) for x in calls] != [(x["name"], x.get("id")) for x in resps]:
                            state.errors.append(f"turn {i + 1}: functionResponse does not match functionCall")
            tools = body.get("tools")
            if tools:
                for fd in tools[0]["functionDeclarations"]:
                    if fd["parameters"]["type"] != "OBJECT":
                        state.errors.append("parameter schema types must be Gemini enums (OBJECT)")

        # ------------------------------------------------------------ fake model
        def _reply(self, body):
            contents = body["contents"]
            question = contents[0]["parts"][0]["text"]
            sql = state.answers.get(question, "SELECT 1 AS x")
            usage = {"promptTokenCount": 1000 + 10 * len(contents), "candidatesTokenCount": 50,
                     "thoughtsTokenCount": 20}
            if "tools" not in body:   # baseline: answer with SQL text
                return {"candidates": [{"content": {"role": "model", "parts": [
                    {"text": "thinking...", "thought": True},
                    {"text": f"```sql\n{sql}\n```"}]}, "finishReason": "STOP"}], "usageMetadata": usage}
            n_model_turns = sum(1 for c in contents if c["role"] == "model")
            sig = "sig-" + uuid.uuid4().hex[:8]
            if n_model_turns == 0:
                call = {"name": "describe_table", "args": {"table": "patients"}, "id": "c1"}
                text = [{"text": "I'll look at the schema first."}]
            elif n_model_turns == 1:
                first = state.first_sql.get(question, sql)
                call = {"name": "run_sql", "args": {"sql": first}, "id": "c2"}
                text = []
            elif n_model_turns == 2 and question in state.first_sql:
                call = {"name": "run_sql", "args": {"sql": sql}, "id": "c3"}
                text = [{"text": "That failed, fixing the query."}]
            else:
                call = {"name": "final_answer", "args": {"sql": sql, "answer": "Here is the answer."}, "id": "c9"}
                text = []
            return {"candidates": [{"content": {"role": "model", "parts": text + [
                {"functionCall": call, "thoughtSignature": sig}]}, "finishReason": "STOP"}],
                "usageMetadata": usage}
    return H


def start():
    state = MockState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state, f"http://127.0.0.1:{server.server_address[1]}/v1beta"

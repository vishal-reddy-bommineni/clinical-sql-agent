"""The full evaluation command, run against the mock Gemini server: scoring, report, resume
after a quota stop."""
import json

import yaml

import src.evaluation.report as report
import src.evaluation.run_eval as run_eval
import src.llm.gemini as gemini
from src.config import QUESTIONS_PATH

QUESTIONS = yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]


def _setup(monkeypatch, tmp_path, url):
    monkeypatch.setattr(gemini, "GEMINI_BASE_URL", url)
    monkeypatch.setattr(gemini, "GEMINI_API_KEY", "AQ.test-key")
    monkeypatch.setattr(run_eval, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(report, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(report, "FIG_DIR", tmp_path / "figures")


def test_eval_scores_resumes_and_reports(mock_gemini, monkeypatch, tmp_path):
    state, url = mock_gemini
    _setup(monkeypatch, tmp_path, url)
    ids = ["q01", "q03", "q23", "q45"]
    for q in QUESTIONS:
        if q["id"] in ("q01", "q23", "q45"):
            state.answers[q["question"]] = q["gold_sql"]          # right
        elif q["id"] == "q03":
            state.answers[q["question"]] = "SELECT gender, COUNT(*) FROM patients WHERE anchor_age > 50 GROUP BY 1"  # runs, wrong numbers

    # first run hits the daily quota after a few calls
    calls = {"n": 0}
    real_post = gemini.GeminiSession._post

    def flaky_post(self, body):
        calls["n"] += 1
        if calls["n"] == 6:
            state.daily_quota = True
        return real_post(self, body)

    monkeypatch.setattr(gemini.GeminiSession, "_post", flaky_post)
    code = run_eval.main(["--ids", *ids, "--run-name", "t"])
    assert code == 2                                               # stopped early, resumable
    path = tmp_path / "runs" / "t" / "results.jsonl"
    first = len(path.read_text().splitlines())
    assert 0 < first < 12

    # second run continues where it stopped
    state.daily_quota = False
    code = run_eval.main(["--ids", *ids, "--run-name", "t"])
    assert code == 0
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(rows) == 12 and len({(r["id"], r["config"]) for r in rows}) == 12
    by = {(r["id"], r["config"]): r["correct"] for r in rows}
    assert all(by[(i, c)] for i in ("q01", "q23", "q45") for c in ("baseline_schema", "baseline_catalog", "agent"))
    assert not any(by[("q03", c)] for c in ("baseline_schema", "baseline_catalog", "agent"))

    md = (tmp_path / "RESULTS.md").read_text()
    assert "75% (" in md and "q03" in md and "wrong result" in md
    assert (tmp_path / "figures" / "accuracy_by_config.png").exists()
    assert not state.errors, state.errors

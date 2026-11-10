"""Run the evaluation: every question x every config, scored against the gold SQL.

Usage:
  python -m src.evaluation.run_eval                      # all 50 questions, all 3 configs
  python -m src.evaluation.run_eval --configs agent      # just the agent
  python -m src.evaluation.run_eval --ids q01 q02        # a few questions (quick test)

Results are appended to reports/runs/<run-name>/results.jsonl as each question finishes, so the
run is RESUMABLE: if the free-tier daily quota runs out, just run the same command again later
(tomorrow) and it continues where it stopped. At the end, the report is regenerated.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime

import yaml

from src.agent.runner import CONFIGS, answer
from src.config import QUESTIONS_PATH, RUNS_DIR
from src.db import run_query
from src.evaluation.compare import results_match
from src.llm import LLMError, QuotaExhausted, current_model_name


def load_questions(ids=None):
    qs = yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]
    return [q for q in qs if not ids or q["id"] in ids]


def default_run_name() -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", current_model_name())


def done_pairs(path):
    out = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out.add((r["id"], r["config"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=CONFIGS)
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--run-name", default=None, help="folder under reports/runs (default: model name)")
    ap.add_argument("--fresh", action="store_true", help="delete previous results for this run first")
    ap.add_argument("--redo", action="store_true",
                    help="rerun the given --ids even if already done (the newest result is used)")
    a = ap.parse_args(argv)

    run_name = a.run_name or default_run_name()
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "results.jsonl"
    if a.fresh and out_path.exists():
        out_path.unlink()
    done = done_pairs(out_path)
    questions = load_questions(a.ids)
    if a.redo and not a.ids:
        raise SystemExit("--redo needs --ids (e.g. --ids q36 q44 --redo)")
    todo = [(q, c) for c in a.configs for q in questions if a.redo or (q["id"], c) not in done]
    print(f"Run '{run_name}' with model {current_model_name()}: {len(todo)} to do, "
          f"{len(done)} already done.")

    gold_cache = {}
    stopped = None
    for i, (q, cfg) in enumerate(todo, 1):
        if q["id"] not in gold_cache:
            g = run_query(q["gold_sql"])
            if not g.ok:
                raise SystemExit(f"Gold SQL for {q['id']} failed: {g.error}")
            gold_cache[q["id"]] = g.df
        try:
            res = answer(q["question"], config=cfg)
        except QuotaExhausted as e:
            stopped = str(e)
            break
        correct = res.error is None and results_match(gold_cache[q["id"]], res.df,
                                                      bool(q.get("order_matters", False)))
        row = {
            "id": q["id"], "config": cfg, "category": q["category"], "difficulty": q["difficulty"],
            "question": q["question"], "correct": bool(correct), "model": current_model_name(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "pred_rows": None if res.df is None else int(len(res.df)),
            "pred_preview": None if res.df is None else res.df.head(5).to_dict("records"),
            **{k: v for k, v in res.to_dict().items() if k not in ("config", "question")},
        }
        with out_path.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        mark = "OK " if correct else ("ERR" if res.error else "XX ")
        print(f"[{i}/{len(todo)}] {mark} {q['id']} {cfg:16s} calls={res.llm_calls} "
              f"{res.seconds:5.1f}s {('| ' + res.error[:80]) if res.error else ''}")
        if res.error and res.error.startswith("LLM error: Gemini rejected the API key"):
            stopped = res.error
            break

    from src.evaluation.report import build_report
    build_report(run_name)
    if stopped:
        print(f"\nStopped early: {stopped}\nRun the same command again later to continue "
              f"(finished questions are kept).")
        return 2
    print("\nAll done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

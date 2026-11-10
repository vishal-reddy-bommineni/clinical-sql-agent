"""Re-score a finished run WITHOUT calling the API: re-runs each saved final SQL against the
current answer key and comparison rules. Use it after fixing a gold query or the scorer.

Usage:  python -m src.evaluation.rescore                 # latest run
        python -m src.evaluation.rescore --run <name>

The original file is kept as results.before_rescore.jsonl. Each row gets "correct_original"
so every change of verdict is traceable; changes are printed.
"""
import argparse
import json
import shutil

import yaml

from src.config import QUESTIONS_PATH, RUNS_DIR
from src.db import run_query
from src.evaluation.compare import results_match


def rescore(run_name=None):
    if run_name is None:
        from src.evaluation.report import pick_run
        run_name = pick_run()
        if run_name is None:
            raise SystemExit("No runs to rescore.")
    path = RUNS_DIR / run_name / "results.jsonl"
    backup = path.with_name("results.before_rescore.jsonl")
    if not backup.exists():
        shutil.copy(path, backup)
    qs = {q["id"]: q for q in yaml.safe_load(QUESTIONS_PATH.read_text())["questions"]}
    gold = {i: run_query(q["gold_sql"]).df for i, q in qs.items()}
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    changed = []
    for r in rows:
        q = qs[r["id"]]
        r.setdefault("correct_original", r["correct"])
        if q["question"] != r["question"]:
            # question was reworded: the old answer cannot be judged against it; rerun with --redo
            r["correct"] = False
            r["stale_question"] = True
            continue
        if not r.get("sql"):
            continue
        res = run_query(r["sql"])
        new = res.ok and results_match(gold[r["id"]], res.df, bool(q.get("order_matters")))
        if res.ok and r.get("error") and r["error"].startswith("Rejected"):
            r["error"] = None          # was rejected only because of the stray 'sql' line
        if bool(new) != bool(r["correct"]):
            changed.append((r["id"], r["config"], r["correct"], bool(new)))
        r["correct"] = bool(new)
    path.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    print(f"Rescored {len(rows)} rows in {path}")
    for c in changed:
        print(f"  {c[0]} {c[1]:16s} {c[2]} -> {c[3]}")
    stale = sorted({r['id'] for r in rows if r.get("stale_question")})
    if stale:
        print(f"Reworded questions to rerun: python -m src.evaluation.run_eval --ids {' '.join(stale)} --redo")
    from src.evaluation.report import build_report
    build_report(run_name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    rescore(ap.parse_args().run)

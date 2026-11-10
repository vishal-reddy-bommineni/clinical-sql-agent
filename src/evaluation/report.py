"""Build reports/RESULTS.md, summary CSVs and figures from one or more evaluation runs.

Usage:  python -m src.evaluation.report                 # latest run
        python -m src.evaluation.report --run <name>
"""
import argparse
import json
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.agent.runner import CONFIGS
from src.config import FIG_DIR, REPORTS_DIR, RUNS_DIR

LABELS = {"baseline_schema": "Baseline: schema only",
          "baseline_catalog": "Baseline: schema + catalog",
          "agent": "Agent: tools + self-correction"}
COLORS = {"baseline_schema": "#9aa5b1", "baseline_catalog": "#5b8db8", "agent": "#1f5f8b"}


def wilson(k, n, z=1.96):
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def load_run(run_name):
    path = RUNS_DIR / run_name / "results.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    # keep the latest row per (id, config)
    return df.sort_values("timestamp").drop_duplicates(["id", "config"], keep="last")


def _text(v) -> str:
    """Missing values come back as None or NaN depending on the pandas version."""
    return v if isinstance(v, str) else ""


def outcome(r):
    if r["correct"]:
        return "correct"
    err = _text(r.get("error"))
    if err.startswith("LLM error") or err.startswith("No SQL") or err.startswith("No query") or err.startswith("Stopped"):
        return "no usable SQL"
    if err:
        return "SQL error"
    return "wrong result"


def build_report(run_name=None):
    if run_name is None:
        runs = sorted(RUNS_DIR.glob("*/results.jsonl"), key=lambda p: p.stat().st_mtime)
        if not runs:
            print("No runs yet.")
            return
        run_name = runs[-1].parent.name
    df = load_run(run_name)
    df["outcome"] = df.apply(outcome, axis=1)
    configs = [c for c in CONFIGS if c in set(df["config"])]
    model = ", ".join(sorted(df["model"].unique()))
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    # ---- overall
    rows = []
    for c in configs:
        d = df[df.config == c]
        k, n = int(d.correct.sum()), len(d)
        lo, hi = wilson(k, n)
        rows.append({"config": LABELS[c], "key": c, "questions": n, "correct": k,
                     "accuracy": k / n if n else math.nan, "ci_low": lo, "ci_high": hi,
                     "sql_errors": int((d.outcome == "SQL error").sum()),
                     "wrong_results": int((d.outcome == "wrong result").sum()),
                     "no_sql": int((d.outcome == "no usable SQL").sum()),
                     "avg_llm_calls": d.llm_calls.mean(), "avg_sql_attempts": d.sql_attempts.mean(),
                     "avg_input_tokens": d.input_tokens.mean(), "avg_output_tokens": d.output_tokens.mean(),
                     "median_seconds": d.seconds.median()})
    overall = pd.DataFrame(rows)
    overall.drop(columns="key").to_csv(REPORTS_DIR / "eval_summary.csv", index=False)

    by_diff = (df.pivot_table(index="difficulty", columns="config", values="correct", aggfunc="mean")
               .reindex(["easy", "medium", "hard"])[configs])
    by_cat = df.pivot_table(index="category", columns="config", values="correct", aggfunc="mean")[configs]
    n_by_cat = df[df.config == configs[0]].groupby("category").size()

    # ---- paired comparison vs agent
    paired = []
    if "agent" in configs:
        wide = df.pivot_table(index="id", columns="config", values="correct", aggfunc="first")
        for c in configs:
            if c == "agent":
                continue
            both = wide[["agent", c]].dropna().astype(bool)
            paired.append({"vs": LABELS[c], "agent_only_correct": int((both.agent & ~both[c]).sum()),
                           f"baseline_only_correct": int((~both.agent & both[c]).sum()),
                           "both_correct": int((both.agent & both[c]).sum()),
                           "both_wrong": int((~both.agent & ~both[c]).sum())})

    # ---- figures
    fig, ax = plt.subplots(figsize=(7, 3.8))
    y = range(len(overall))
    ax.barh(list(y), overall.accuracy * 100, color=[COLORS[k] for k in overall.key],
            xerr=[(overall.accuracy - overall.ci_low) * 100, (overall.ci_high - overall.accuracy) * 100],
            capsize=4)
    for i, r in overall.iterrows():
        ax.text(r.ci_high * 100 + 2, i, f"{r.accuracy:.0%} ({r.correct}/{r.questions})", va="center", fontsize=9)
    ax.set_yticks(list(y), overall.config)
    ax.invert_yaxis()
    ax.set_xlim(0, 125)
    ax.set_xlabel("Execution accuracy (%), 95% Wilson CI")
    ax.set_title(f"Text-to-SQL accuracy on 50 MIMIC-IV questions ({model})", fontsize=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "accuracy_by_config.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.0))
    (by_diff * 100).rename(columns=LABELS).plot.bar(ax=ax, color=[COLORS[c] for c in configs], rot=0)
    ax.set_ylabel("Accuracy (%)")
    ax.set_xlabel("")
    ax.set_ylim(0, 105)
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3)
    ax.set_title("Accuracy by question difficulty", fontsize=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "accuracy_by_difficulty.png", dpi=160)
    plt.close(fig)

    # ---- markdown
    pct = lambda v: "-" if pd.isna(v) else f"{v:.0%}"
    L = [f"# Evaluation results (auto-generated, do not edit)", "",
         f"Run: `{run_name}` | model: **{model}** | questions per config: "
         f"{', '.join(f'{LABELS[c]} {int((df.config == c).sum())}' for c in configs)}", "",
         "| Config | Accuracy (95% CI) | Correct | Wrong result | SQL error | No usable SQL | Avg LLM calls | Avg SQL runs | Avg input tokens | Median seconds |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in overall.iterrows():
        L.append(f"| {r.config} | {r.accuracy:.0%} ({r.ci_low:.0%}-{r.ci_high:.0%}) | {r.correct}/{r.questions} | "
                 f"{r.wrong_results} | {r.sql_errors} | {r.no_sql} | {r.avg_llm_calls:.1f} | {r.avg_sql_attempts:.1f} | "
                 f"{r.avg_input_tokens:,.0f} | {r.median_seconds:.1f} |")
    L += ["", "![accuracy](figures/accuracy_by_config.png)", "", "## By difficulty", "",
          "| Difficulty | " + " | ".join(LABELS[c] for c in configs) + " |",
          "|---|" + "---|" * len(configs)]
    for d, r in by_diff.iterrows():
        L.append(f"| {d} | " + " | ".join(pct(r[c]) for c in configs) + " |")
    L += ["", "## By category", "", "| Category (n) | " + " | ".join(LABELS[c] for c in configs) + " |",
          "|---|" + "---|" * len(configs)]
    for cat, r in by_cat.iterrows():
        L.append(f"| {cat} ({n_by_cat.get(cat, 0)}) | " + " | ".join(pct(r[c]) for c in configs) + " |")
    if paired:
        L += ["", "## Paired comparison (same questions)", "",
              "| Agent vs | Agent right, baseline wrong | Baseline right, agent wrong | Both right | Both wrong |",
              "|---|---|---|---|---|"]
        for p in paired:
            L.append(f"| {p['vs']} | {p['agent_only_correct']} | {p['baseline_only_correct']} | "
                     f"{p['both_correct']} | {p['both_wrong']} |")
    L += ["", "## Every miss", "", "| Question | Config | Outcome | Detail |", "|---|---|---|---|"]
    for _, r in df[~df.correct].sort_values(["id", "config"]).iterrows():
        detail = (_text(r.get("error")) or f"returned {r.get('pred_rows')} row(s): {json.dumps(r.get('pred_preview'), default=str)[:160]}")
        detail = str(detail).replace("|", "/").replace("\n", " ")
        L.append(f"| {r.id} {r.question[:70]} | {r.config} | {r.outcome} | {detail[:200]} |")
    (REPORTS_DIR / "RESULTS.md").write_text("\n".join(L) + "\n")
    print(f"Report written: {REPORTS_DIR / 'RESULTS.md'}")
    print(overall[["config", "correct", "questions", "accuracy"]].to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    build_report(ap.parse_args().run)

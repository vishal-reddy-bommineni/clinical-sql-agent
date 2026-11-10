"""Clinical SQL Agent: ask questions about hospital data in plain English.

Run:  streamlit run app.py
"""
import altair as alt
import pandas as pd
import streamlit as st

from src import catalog
from src.agent.runner import answer
from src.build_db import ensure_db
from src.config import FIG_DIR, LLM_PROVIDER, REPORTS_DIR
from src.llm import QuotaExhausted, current_model_name

st.set_page_config(page_title="Clinical SQL Agent", layout="wide")

EXAMPLES = [
    "What is the in-hospital mortality rate for each insurance type?",
    "What are the 5 most common diagnoses by number of admissions?",
    "Compare average length of stay for admissions with and without sepsis.",
    "What is the average ICU heart rate by first care unit?",
    "How many admissions were followed by a readmission within 30 days?",
    "Which organisms grow most often in urine cultures?",
]
MODES = {"Agent (explores, runs and fixes SQL)": "agent",
         "Baseline (one shot, documented schema)": "baseline_catalog"}


@st.cache_resource
def _setup():
    ensure_db()
    return True


@st.cache_data(show_spinner=False, max_entries=200)
def _ask(question: str, config: str):
    """Cached, so repeating a question costs no API quota."""
    r = answer(question, config=config)
    return {"sql": r.sql, "answer": r.answer, "error": r.error, "steps": r.steps,
            "df": r.df, "llm_calls": r.llm_calls, "seconds": r.seconds,
            "tokens": r.input_tokens + r.output_tokens}


def auto_chart(df: pd.DataFrame):
    if df is None or len(df) < 2 or len(df) > 40 or df.shape[1] < 2:
        return None
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not c.endswith("_id")]
    cat = [c for c in df.columns if df[c].dtype == object]
    tim = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    if tim and num:
        return alt.Chart(df).mark_line(point=True).encode(x=tim[0], y=num[0]).properties(height=280)
    if cat and num:
        return (alt.Chart(df).mark_bar(color="#1f5f8b")
                .encode(y=alt.Y(f"{cat[0]}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320)),
                        x=alt.X(f"{num[0]}:Q"), tooltip=list(df.columns))
                .properties(height=max(120, 28 * len(df))))
    return None


def show_result(res: dict):
    if res["error"] and res["df"] is None:
        st.error(res["error"])
        return
    if res["answer"]:
        st.markdown(res["answer"])
    if res["df"] is not None:
        chart = auto_chart(res["df"])
        if chart is not None:
            st.altair_chart(chart, width="stretch")
        st.dataframe(res["df"], hide_index=True, width="stretch")
    with st.expander("SQL"):
        st.code(res["sql"] or "-", language="sql")
    if res["steps"]:
        with st.expander(f"How it got there ({res['llm_calls']} model calls, {res['seconds']:.1f}s)"):
            for s in res["steps"]:
                if s["type"] == "thought":
                    st.markdown(f"*{s['text']}*")
                elif s["type"] in ("tool", "final"):
                    name = s.get("name", "final_answer")
                    args = s.get("args", {})
                    st.markdown(f"**{name}** `{str(args)[:300]}`")
                    if s.get("output"):
                        st.code(s["output"][:1200])
                elif s["type"] == "fallback":
                    st.markdown("*Used the last successful query.*")


# ------------------------------------------------------------------ layout
_setup()
st.title("Clinical SQL Agent")
st.caption("Ask questions about the MIMIC-IV Clinical Database Demo (100 de-identified ICU patients, "
           "31 tables) in plain English. An LLM agent writes read-only SQL, runs it, checks the result "
           "and answers. Portfolio project, not for clinical use.")

with st.sidebar:
    st.markdown(f"**Model:** `{current_model_name()}` ({LLM_PROVIDER})")
    mode_label = st.radio("Mode", list(MODES))
    st.markdown("**Try an example**")
    for ex in EXAMPLES:
        if st.button(ex, width="stretch"):
            st.session_state["pending"] = ex
    st.caption("Free API tiers allow only a few requests per minute; the agent uses about 3-6 per "
               "question, so an answer can take 30-60 seconds.")

tab_ask, tab_eval, tab_data = st.tabs(["Ask", "Evaluation", "Data guide"])

with tab_ask:
    st.session_state.setdefault("history", [])
    for q, res in st.session_state["history"]:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            show_result(res)
    question = st.chat_input("e.g. How many patients were prescribed insulin?")
    question = question or st.session_state.pop("pending", None)
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Thinking, exploring tables, running SQL..."):
                try:
                    res = _ask(question, MODES[mode_label])
                except QuotaExhausted as e:
                    res = {"error": f"The free API quota is used up for today ({e}). Try again later.",
                           "df": None, "steps": [], "sql": "", "answer": "", "llm_calls": 0,
                           "seconds": 0, "tokens": 0}
            show_result(res)
        st.session_state["history"].append((question, res))

with tab_eval:
    results = REPORTS_DIR / "RESULTS.md"
    if results.exists():
        for fig in ("accuracy_by_config.png", "accuracy_by_difficulty.png"):
            if (FIG_DIR / fig).exists():
                st.image(str(FIG_DIR / fig))
        st.markdown(results.read_text().replace("![accuracy](figures/accuracy_by_config.png)", ""))
    else:
        st.info("No evaluation run yet. Run: python -m src.evaluation.run_eval")
    gold = REPORTS_DIR / "gold_answers.md"
    if gold.exists():
        with st.expander("Answer key: all 50 questions with gold SQL"):
            st.markdown(gold.read_text())

with tab_data:
    st.markdown("**Database notes**")
    st.markdown(catalog.notes_text())
    t = st.selectbox("Table", catalog.table_names(), index=catalog.table_names().index("admissions"))
    st.code(catalog.describe_table_text(t))

"""Tools the agent can call. Each returns plain text that the model reads."""
import re

from src import catalog
from src.config import MAX_ROWS_SHOWN_TO_LLM
from src.db import run_query
from src.llm import ToolSpec

TOOLS = [
    ToolSpec(
        name="describe_table",
        description="Show the columns, types, descriptions and known gotchas of one table.",
        parameters={"type": "object", "properties": {
            "table": {"type": "string", "description": "Table name, e.g. 'admissions'."}},
            "required": ["table"]}),
    ToolSpec(
        name="search_values",
        description=("Find how values are written in a text column: returns distinct values containing "
                     "the search text (case-insensitive) with row counts. Use it for lab names, drug "
                     "names, diagnosis titles, care units, categories."),
        parameters={"type": "object", "properties": {
            "table": {"type": "string", "description": "Table name, e.g. 'd_labitems'."},
            "column": {"type": "string", "description": "Text column, e.g. 'label'."},
            "contains": {"type": "string", "description": "Text to search for, e.g. 'creatinine'."}},
            "required": ["table", "column", "contains"]}),
    ToolSpec(
        name="run_sql",
        description=(f"Run a read-only DuckDB SELECT query and see up to {MAX_ROWS_SHOWN_TO_LLM} rows of "
                     "the result (and the total row count), or the error message."),
        parameters={"type": "object", "properties": {
            "sql": {"type": "string", "description": "One SELECT query."}},
            "required": ["sql"]}),
    ToolSpec(
        name="final_answer",
        description="Finish: give the final SQL query that answers the question and a short plain-English answer.",
        parameters={"type": "object", "properties": {
            "sql": {"type": "string", "description": "The final SELECT query."},
            "answer": {"type": "string", "description": "One or two sentences answering the question with the numbers."}},
            "required": ["sql", "answer"]}),
]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def describe_table(table: str) -> str:
    return catalog.describe_table_text(str(table).strip().lower())


def search_values(table: str, column: str, contains: str) -> str:
    table, column = str(table).strip().lower(), str(column).strip()
    sch = catalog.schema()
    if table not in sch:
        return f"Unknown table '{table}'."
    cols = {c for c, _ in sch[table]["columns"]}
    if not _IDENT.match(column) or column not in cols:
        return f"Unknown column '{column}' in {table}. Columns: {', '.join(sorted(cols))}"
    pattern = "%" + str(contains).replace("'", "''") + "%"
    extra = ", itemid" if table in ("d_labitems", "d_items") and column != "itemid" else ""
    extra += ", fluid" if table == "d_labitems" and column != "fluid" else ""
    extra += ", icd_code, icd_version" if table.startswith("d_icd") and column == "long_title" else ""
    sql = (f'SELECT "{column}"{extra}, COUNT(*) AS n FROM {table} '
           f"WHERE CAST(\"{column}\" AS VARCHAR) ILIKE '{pattern}' GROUP BY ALL ORDER BY n DESC LIMIT 25")
    r = run_query(sql)
    if not r.ok:
        return f"Error: {r.error}"
    if r.df.empty:
        return f"No values in {table}.{column} contain '{contains}'."
    return r.df.to_string(index=False, max_colwidth=80)


def run_sql_tool(sql: str) -> tuple[str, object]:
    """Returns (text for the model, QueryResult)."""
    r = run_query(sql)
    if not r.ok:
        return f"Error: {r.error}", r
    n = len(r.df)
    shown = r.df.head(MAX_ROWS_SHOWN_TO_LLM)
    text = f"{n}{'+' if r.truncated else ''} row(s)." + ("" if n == 0 else "\n" + shown.to_string(index=False, max_colwidth=60))
    if n > MAX_ROWS_SHOWN_TO_LLM:
        text += f"\n... ({n - MAX_ROWS_SHOWN_TO_LLM} more rows not shown)"
    return text, r

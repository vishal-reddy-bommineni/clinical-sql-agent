"""Safe, read-only query execution.

Three layers of protection, so a model can never change or leak anything:
1. sql_guard: the SQL must parse as exactly one SELECT (or WITH ... SELECT / UNION) statement,
   with no file-reading functions.
2. The DuckDB connection is opened read_only=True with external access disabled
   (no reading local files or URLs).
3. A timeout interrupts long queries; results are capped at MAX_RESULT_ROWS.
"""
import threading
import time
from dataclasses import dataclass, field

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

from src.config import DB_PATH, MAX_RESULT_ROWS, QUERY_TIMEOUT_SECONDS

BLOCKED_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
    "read_blob", "glob", "parquet_scan", "csv_scan", "sniff_csv", "query", "query_table",
}
BLOCKED_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Copy, exp.Merge, exp.Pragma, exp.Set, exp.Use, exp.Attach, exp.Detach, exp.TruncateTable,
)


class UnsafeSQL(ValueError):
    pass


def clean_sql(sql: str) -> str:
    sql = (sql or "").strip()
    if sql.startswith("```"):
        sql = sql.strip("`")
    lines = sql.strip().splitlines()
    # models sometimes put the language name on its own line: "sql\nSELECT ..."
    while lines and lines[0].strip().lower() in ("sql", "duckdb", "```sql", "```"):
        lines = lines[1:]
    return "\n".join(lines).strip().rstrip(";").strip()


def check_sql(sql: str) -> str:
    """Return the cleaned SQL if it is a single read-only query, else raise UnsafeSQL."""
    sql = clean_sql(sql)
    if not sql:
        raise UnsafeSQL("Empty query.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"Could not parse SQL: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise UnsafeSQL("Only one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise UnsafeSQL(f"Only SELECT queries are allowed (got {type(tree).__name__}).")
    for node in tree.walk():
        if isinstance(node, BLOCKED_NODES):
            raise UnsafeSQL(f"Statement type {type(node).__name__} is not allowed.")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in BLOCKED_FUNCTIONS:
                raise UnsafeSQL(f"Function {name} is not allowed.")
        if isinstance(node, exp.Table) and (isinstance(node.this, exp.Literal)
                                            or any(ch in node.name for ch in "/\\:")):
            raise UnsafeSQL("Reading files by path is not allowed.")
    return sql


def connect():
    if not DB_PATH.exists():          # first run: build data/mimic_demo.duckdb from the CSVs
        from src.build_db import build
        build(quiet=True)
    return duckdb.connect(str(DB_PATH), read_only=True,
                          config={"enable_external_access": False})


@dataclass
class QueryResult:
    sql: str
    df: pd.DataFrame | None = None
    error: str | None = None
    truncated: bool = False
    seconds: float = 0.0
    columns: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def run_query(sql: str, max_rows: int = MAX_RESULT_ROWS,
              timeout: float = QUERY_TIMEOUT_SECONDS) -> QueryResult:
    """Validate and execute. Never raises: errors are returned in QueryResult.error so the
    agent can read them and fix its query."""
    t0 = time.time()
    try:
        sql = check_sql(sql)
    except UnsafeSQL as e:
        return QueryResult(sql=sql, error=f"Rejected: {e}", seconds=time.time() - t0)
    con = connect()
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(rows[:max_rows], columns=cols)
        return QueryResult(sql=sql, df=df, truncated=len(rows) > max_rows,
                           seconds=time.time() - t0, columns=cols)
    except duckdb.InterruptException:
        return QueryResult(sql=sql, error=f"Query timed out after {timeout:.0f}s.",
                           seconds=time.time() - t0)
    except duckdb.Error as e:
        msg = str(e).strip().splitlines()
        return QueryResult(sql=sql, error=" ".join(msg[:4])[:800], seconds=time.time() - t0)
    finally:
        timer.cancel()
        con.close()

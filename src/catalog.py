"""Turns the database schema and catalog.yaml into text for prompts and tools."""
from functools import lru_cache

import yaml

from src.config import CATALOG_PATH
from src.db import connect


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    return yaml.safe_load(CATALOG_PATH.read_text())


@lru_cache(maxsize=1)
def schema() -> dict:
    """{table: [(column, type), ...]} read from the database itself."""
    con = connect()
    rows = con.execute("""
        SELECT table_name, column_name, data_type FROM information_schema.columns
        ORDER BY table_name, ordinal_position""").fetchall()
    counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
              for t in {r[0] for r in rows}}
    con.close()
    out: dict = {}
    for t, c, d in rows:
        out.setdefault(t, {"columns": [], "rows": counts[t]})["columns"].append((c, d))
    return out


def table_names() -> list[str]:
    return sorted(schema())


def ddl_text() -> str:
    """Plain CREATE TABLE statements: what a model gets with no documentation."""
    parts = []
    for t, info in sorted(schema().items()):
        cols = ",\n  ".join(f"{c} {d}" for c, d in info["columns"])
        parts.append(f"CREATE TABLE {t} (\n  {cols}\n);")
    return "\n".join(parts)


def notes_text() -> str:
    return "\n".join(f"- {n}" for n in load_catalog()["database_notes"])


def overview_text() -> str:
    """One line per table: name, rows, description. Used as the agent's starting map."""
    cat = load_catalog()["tables"]
    lines = []
    for t, info in sorted(schema().items()):
        desc = cat.get(t, {}).get("description", "")
        lines.append(f"- {t} ({info['rows']:,} rows): {desc}")
    return "\n".join(lines)


def describe_table_text(table: str) -> str:
    """Columns with types and descriptions, plus known gotchas, for one table."""
    sch = schema()
    if table not in sch:
        return f"Unknown table '{table}'. Valid tables: {', '.join(sorted(sch))}"
    meta = load_catalog()["tables"].get(table, {})
    col_docs = meta.get("columns", {}) or {}
    lines = [f"Table {table} ({sch[table]['rows']:,} rows): {meta.get('description', '')}", "Columns:"]
    for c, d in sch[table]["columns"]:
        doc = col_docs.get(c, "")
        lines.append(f"  - {c} {d}" + (f": {doc}" if doc else ""))
    for g in meta.get("gotchas", []) or []:
        lines.append(f"Gotcha: {g}")
    return "\n".join(lines)


def full_catalog_text() -> str:
    """Every table, documented: the 'stuff everything in the prompt' approach."""
    return "\n\n".join(describe_table_text(t) for t in table_names())

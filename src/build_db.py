"""Load the MIMIC-IV Clinical Database Demo (.csv.gz files) into one DuckDB file.

Usage:  python -m src.build_db
Creates data/mimic_demo.duckdb with every table from hosp/ and icu/ (31 tables), and writes
reports/table_row_counts.csv. Takes a few seconds.
"""
import duckdb

from src.config import DB_PATH, RAW_DIR, REPORTS_DIR

# Columns that look numeric in some rows but are codes and must stay text.
FORCE_VARCHAR = {
    "icd_code", "drg_code", "hcpcs_cd", "ndc", "gsn", "formulary_drug_cd", "poe_id",
    "pharmacy_id", "emar_id", "order_provider_id", "caregiver_id", "provider_id",
}


def build(db_path=DB_PATH, raw_dir=RAW_DIR, quiet=False) -> None:
    if not raw_dir.exists():
        raise SystemExit(f"Missing {raw_dir}. See data/README.md.")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    counts = []
    for module in ("hosp", "icu"):
        for f in sorted((raw_dir / module).glob("*.csv.gz")):
            table = f.name.replace(".csv.gz", "")
            header = con.execute(
                f"SELECT * FROM read_csv('{f.as_posix()}', header=true, all_varchar=true) LIMIT 0"
            ).df().columns
            types = {c: "VARCHAR" for c in header if c in FORCE_VARCHAR}
            types_sql = f", types = {types}" if types else ""   # e.g. {'icd_code': 'VARCHAR'}
            con.execute(f"""
                CREATE TABLE {table} AS
                SELECT * FROM read_csv('{f.as_posix()}', header = true, sample_size = -1{types_sql})
            """)
            con.execute(f"COMMENT ON TABLE {table} IS 'MIMIC-IV demo, module {module}'")
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts.append((module, table, n))
            if not quiet:
                print(f"{module}.{table:22s} {n:>9,} rows")
    con.close()
    if db_path.exists():
        db_path.unlink()
    tmp.rename(db_path)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORTS_DIR / "table_row_counts.csv", "w") as fh:
        fh.write("module,table,rows\n")
        fh.writelines(f"{m},{t},{n}\n" for m, t, n in counts)
    if not quiet:
        print(f"\nBuilt {db_path} ({len(counts)} tables)")


def ensure_db() -> None:
    """Build the database if it does not exist yet (used by the app on first start)."""
    if not DB_PATH.exists():
        build(quiet=True)


if __name__ == "__main__":
    build()

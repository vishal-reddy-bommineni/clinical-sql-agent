"""Execution accuracy: is the model's query result the same as the gold query result?

Fair but strict rules:
- Column NAMES and column ORDER do not matter, and extra columns are allowed: every gold column
  must match some predicted column, value by value.
- Row ORDER matters only when the question asks for a ranking (order_matters: true).
- Row COUNT must match exactly.
- Whole numbers must match exactly. Decimals may differ by rounding: |a - b| <= max(0.051, 0.5% of b)
  (so 6.88 vs 6.8812 passes, 43 vs 44 fails).
- Text is compared case-insensitively after trimming spaces.
"""
import math
from decimal import Decimal

import numpy as np
import pandas as pd


def _norm(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating, Decimal)):
        f = float(v)
        return int(f) if f.is_integer() else f
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if pd.isna(v) if not isinstance(v, (list, dict)) else False:
        return None
    return str(v).strip().lower()


def values_equal(gold, pred) -> bool:
    g, p = _norm(gold), _norm(pred)
    if g is None or p is None:
        return g is None and p is None
    g_num = isinstance(g, (int, float))
    p_num = isinstance(p, (int, float))
    if g_num and p_num:
        if isinstance(g, int) and isinstance(p, int):
            return g == p
        tol = max(0.051, 0.005 * abs(g))
        return abs(float(g) - float(p)) <= tol
    if g_num != p_num:
        # numbers stored as text, e.g. '120'
        try:
            return values_equal(float(g), float(p))
        except (TypeError, ValueError):
            return False
    return g == p


def _column_matches(gold_col, pred_col, ordered: bool) -> bool:
    if ordered:
        return all(values_equal(g, p) for g, p in zip(gold_col, pred_col))
    # unordered: greedy multiset match
    remaining = list(pred_col)
    for g in gold_col:
        for i, p in enumerate(remaining):
            if values_equal(g, p):
                remaining.pop(i)
                break
        else:
            return False
    return True


def results_match(gold: pd.DataFrame, pred: pd.DataFrame | None, order_matters: bool = False) -> bool:
    if pred is None or gold is None:
        return False
    if len(gold) != len(pred):
        return False
    if len(gold) == 0:
        return True
    if not order_matters:
        # sort both by a canonical row key so rows stay aligned across columns
        gold = _sort_rows(gold)
        pred_candidates = _sort_rows(pred)
    else:
        pred_candidates = pred
    pred_cols = [pred_candidates[c].tolist() for c in pred_candidates.columns]
    used = set()
    for gc in gold.columns:
        g = gold[gc].tolist()
        for j, p in enumerate(pred_cols):
            if j in used:
                continue
            if _column_matches(g, p, ordered=True):
                used.add(j)
                break
        else:
            if order_matters:
                return False
            # unordered fallback: rows may be sorted differently by an extra/renamed key column
            return _row_set_match(gold, pred)
    return True


def _sort_rows(df: pd.DataFrame) -> pd.DataFrame:
    key = df.apply(lambda r: tuple("" if _norm(v) is None else str(_norm(v)) for v in r), axis=1)
    return df.iloc[np.argsort(key.values, kind="stable")].reset_index(drop=True)


def _row_set_match(gold: pd.DataFrame, pred: pd.DataFrame) -> bool:
    """Unordered rows: find a column mapping, then match rows as a multiset."""
    from itertools import permutations
    gcols, pcols = list(gold.columns), list(pred.columns)
    if len(pcols) < len(gcols) or len(pcols) > 8:
        return False
    for perm in permutations(range(len(pcols)), len(gcols)):
        remaining = [tuple(r) for r in pred.iloc[:, list(perm)].itertuples(index=False)]
        ok = True
        for grow in gold.itertuples(index=False):
            for i, prow in enumerate(remaining):
                if all(values_equal(g, p) for g, p in zip(grow, prow)):
                    remaining.pop(i)
                    break
            else:
                ok = False
                break
        if ok:
            return True
    return False

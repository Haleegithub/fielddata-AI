"""
FieldData AI - Stage 5: analytics (statistics, trends, geography, quality indicators, key findings).

Only values that passed validation are used: cells with an error are left out, and duplicate records are dropped.
Every sentence in "findings" is built from numbers computed here, so nothing is invented.
"""
import numpy as np
import pandas as pd

from .validation import MISSING_TOKENS, detect_roles, jsonable

SKIP_ROLES = ("id", "latitude", "longitude", "phone", "submission_time", "interview_date")
GPS_TYPES = {"invalid_gps", "gps_state_mismatch"}


def _valid_table(df, issues, roles):
    """Text table with missing values and error cells set to NaN, duplicates dropped. Returns (table, cells_excluded, duplicates)."""
    v = df.apply(lambda s: s.str.strip())
    v = v.mask(v.apply(lambda s: s.str.lower().isin(MISSING_TOKENS)))
    err = issues[issues.severity == "error"].copy()
    err["row"] = err["row"].astype(int)
    dup_rows = sorted(set(err.loc[err.issue_type == "duplicate_record", "row"] - 2) & set(v.index))
    excluded = 0
    lat, lon = roles.get("latitude"), roles.get("longitude")
    for col, grp in err[(err.column != "*") & (err.issue_type != "duplicate_record")].groupby("column"):
        idx = [i for i in grp.row - 2 if i in v.index]
        cols = [col] + ([lon] if col == lat and lon and set(grp.issue_type) & GPS_TYPES else [])
        for c in cols:
            if c in v.columns:
                excluded += int(v.loc[idx, c].notna().sum())
                v.loc[idx, c] = np.nan
    return v.drop(index=dup_rows), excluded, len(dup_rows)


def _stats(s):
    n = _num_series(s)
    q1, med, q3 = n.quantile([.25, .5, .75])
    return {"n": int(n.count()), "mean": round(float(n.mean()), 2), "median": float(med), "std": round(float(n.std() or 0), 2),
            "min": float(n.min()), "max": float(n.max()), "q1": float(q1), "q3": float(q3)}


def _num_series(s):
    return pd.to_numeric(s, errors="coerce").dropna()


def _histogram(s, bins=10):
    n = _num_series(s)
    if n.nunique() <= 12 and (n == n.round()).all():                      # small whole numbers: one bar per value
        vc = n.astype(int).value_counts().sort_index()
        return [[str(k), int(c)] for k, c in vc.items()]
    counts, edges = np.histogram(n, bins=bins)
    fmt = (lambda x: f"{x:.0f}") if (edges[-1] - edges[0]) >= 20 else (lambda x: f"{x:.1f}")
    return [[f"{fmt(edges[i])}–{fmt(edges[i + 1])}", int(c)] for i, c in enumerate(counts)]


def _top(s, k=10):
    vc = s.dropna().value_counts()
    total = int(vc.sum())
    return {"total": total, "missing": int(s.isna().sum()), "unique": int(len(vc)),
            "values": [[str(a), int(b), round(100 * b / total, 1)] for a, b in vc.head(k).items()]}


def _rate_table(df, group_col, error_rows, k=15):
    """Records, records with errors and error rate for each group (all records, including invalid ones)."""
    g = df[group_col].str.strip().replace("", np.nan)
    rows = pd.DataFrame({"g": g, "err": [i + 2 in error_rows for i in df.index]}).dropna(subset=["g"])
    t = rows.groupby("g").agg(records=("err", "size"), with_errors=("err", "sum"))
    t["error_rate_pct"] = (100 * t.with_errors / t.records).round(1)
    t = t.sort_values(["error_rate_pct", "records"], ascending=False).head(k)
    return [[str(i), int(r.records), int(r.with_errors), float(r.error_rate_pct)] for i, r in t.iterrows()]


def analyse(df, issues, roles=None):
    roles = roles or detect_roles(list(df.columns))
    valid, excluded, dups = _valid_table(df, issues, roles)
    n_all, n_used = len(df), len(valid)
    out = {"roles": roles}

    # ---- overview ---------------------------------------------------------------------------------------
    err_rows = set(issues.loc[issues.severity == "error", "row"].astype(int))
    date_col = roles.get("interview_date") or roles.get("submission_time")
    dates = pd.to_datetime(valid[date_col], errors="coerce", format="mixed").dt.normalize().dropna() if date_col else pd.Series(dtype="datetime64[ns]")
    filled = 100 * float(valid.notna().values.mean()) if n_used else 0.0
    out["overview"] = {
        "records_total": n_all, "records_used": n_used, "duplicates_excluded": dups, "cells_excluded": excluded,
        "records_with_errors": len(err_rows), "columns": df.shape[1], "completeness_pct": round(filled, 1),
        "unusual_values_kept": int((issues.issue_type == "unusual_value").sum()),
        "date_from": str(dates.min().date()) if len(dates) else None, "date_to": str(dates.max().date()) if len(dates) else None,
        "days": int((dates.max() - dates.min()).days + 1) if len(dates) else 0,
        "enumerators": int(valid[roles["enumerator"]].nunique()) if roles.get("enumerator") else None,
        "states": int(valid[roles["state"]].nunique()) if roles.get("state") else None}

    # ---- numeric and categorical columns ------------------------------------------------------------------
    skip = {roles[r] for r in SKIP_ROLES if r in roles}
    numeric, categorical = {}, {}
    for c in valid.columns:
        if c in skip or c.startswith("_"):
            continue
        s = valid[c].dropna()
        if len(s) == 0:
            continue
        if pd.to_numeric(s, errors="coerce").notna().mean() >= 0.9:
            numeric[c] = {**_stats(s), "histogram": _histogram(s)}
        elif s.nunique() <= 20:
            categorical[c] = _top(valid[c])
    out["numeric"], out["categorical"] = numeric, categorical

    # ---- submission trend ------------------------------------------------------------------------------------
    out["trend"] = None
    if len(dates):
        daily = dates.value_counts().sort_index()
        full = daily.reindex(pd.date_range(daily.index.min(), daily.index.max()), fill_value=0)
        peak = daily.idxmax()
        out["trend"] = {"column": date_col, "daily": [[str(d.date()), int(c)] for d, c in full.items()],
                        "average_per_day": round(float(full.mean()), 1), "peak_day": str(peak.date()), "peak_count": int(daily.max())}

    # ---- geography ---------------------------------------------------------------------------------------------
    out["geography"] = None
    if roles.get("state"):
        sc = roles["state"]
        has_gps = valid[roles["latitude"]].notna() & valid[roles["longitude"]].notna() if roles.get("latitude") and roles.get("longitude") else pd.Series(False, index=valid.index)
        by_state = _top(valid[sc], 30)
        by_state["gps_valid"] = {k: int(v) for k, v in has_gps.groupby(valid[sc]).sum().items()}
        geo = {"by_state": by_state, "gps_valid_records": int(has_gps.sum()),
               "gps_coverage_pct": round(100 * float(has_gps.mean()), 1) if n_used else 0.0}
        if roles.get("lga"):
            pairs = valid.dropna(subset=[sc, roles["lga"]]).groupby([sc, roles["lga"]]).size().sort_values(ascending=False).head(15)
            geo["top_lgas"] = [[f"{lga}, {st}", int(n)] for (st, lga), n in pairs.items()]
        out["geography"] = geo

    # ---- data quality indicators (all records, as collected) ---------------------------------------------------------
    quality = {"missing_by_column": []}
    raw_missing = df.apply(lambda s: s.str.strip().str.lower().isin(MISSING_TOKENS))
    m = raw_missing.sum().sort_values(ascending=False)
    quality["missing_by_column"] = [[c, int(n), round(100 * n / max(n_all, 1), 1)] for c, n in m.head(10).items() if n > 0]
    quality["by_enumerator"] = _rate_table(df, roles["enumerator"], err_rows) if roles.get("enumerator") else None
    quality["by_state"] = _rate_table(df, roles["state"], err_rows) if roles.get("state") else None
    out["quality"] = quality
    out["findings"] = _findings(out)
    return jsonable(out)


def _findings(a):
    o, f = a["overview"], []
    if o["date_from"]:
        f.append(f"{o['records_total']:,} records were collected between {o['date_from']} and {o['date_to']} ({o['days']} days).")
    else:
        f.append(f"{o['records_total']:,} records were uploaded.")
    if o["records_with_errors"]:
        f.append(f"{o['records_with_errors']:,} records ({100 * o['records_with_errors'] / o['records_total']:.1f}%) contain at least one error. "
                 f"Statistics below leave out {o['cells_excluded']:,} invalid values and {o['duplicates_excluded']:,} duplicate records.")
    if o["unusual_values_kept"]:
        f.append(f"{o['unusual_values_kept']:,} unusual values were kept in the statistics because they may be real (for example very large households). Check them in the data quality tab.")
    g = a["geography"]
    if g and g["by_state"]["values"]:
        s = g["by_state"]["values"][0]
        f.append(f"{s[0]} has the most submissions: {s[1]:,} ({s[2]}% of records with a state).")
        f.append(f"{g['gps_coverage_pct']}% of records have a valid GPS location.")
    t = a["trend"]
    if t:
        f.append(f"The busiest day was {t['peak_day']} with {t['peak_count']} submissions; the average is {t['average_per_day']} per day.")
    for role in ("age", "household_size"):
        col = a["roles"].get(role)
        if col and col in a["numeric"]:
            n = a["numeric"][col]
            f.append(f"Median {col.replace('_', ' ')} is {n['median']:g} (range {n['min']:g} to {n['max']:g}, {n['n']:,} valid values).")
    q = a["quality"]
    if q["by_enumerator"]:
        e = q["by_enumerator"][0]
        f.append(f"Enumerator {e[0]} has the highest error rate: {e[3]}% of its {e[1]} records contain errors.")
    if q["missing_by_column"]:
        c = q["missing_by_column"][0]
        f.append(f"The column '{c[0]}' has the most blank answers: {c[1]:,} ({c[2]}% of records).")
    return f

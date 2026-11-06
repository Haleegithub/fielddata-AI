"""
FieldData AI - Stage 4: reviewing, applying and exporting corrections (business logic, no web framework).
The original upload is never changed. Accepted corrections create a separate cleaned copy and a change log.
"""
import io
import json
import re

import pandas as pd

from fielddata import cleaning
from fielddata.validation import jsonable, validate

from . import datasets
from .services import ServiceError

STATUSES = ("pending", "accepted", "rejected")
MAX_IDS = 5000
EXPORTS = {"original": None, "cleaned": "_clean", "changes": "_changes", "followup": "_followup"}
EXPORT_NAMES = {"original": "original", "cleaned": "cleaned", "changes": "change_log", "followup": "follow_up_list"}
FIELDS = "id, row_no, record_id, enumerator, column_name, column2, action, old_value, new_value, rule, confidence, reason, status"


def _check_dataset(conn, user_id, dataset_id):
    return datasets._row(conn, user_id, dataset_id)


def _summary(conn, dataset_id):
    rows = conn.execute("SELECT rule, confidence, status, COUNT(*) AS n FROM corrections WHERE dataset_id = ? "
                        "GROUP BY rule, confidence, status", (dataset_id,)).fetchall()
    by_rule = {}
    for r in rows:
        d = by_rule.setdefault(r["rule"], {"label": cleaning.RULES.get(r["rule"], r["rule"]), "confidence": r["confidence"],
                                           "pending": 0, "accepted": 0, "rejected": 0})
        d[r["status"]] += r["n"]
    totals = {s: sum(d[s] for d in by_rule.values()) for s in STATUSES}
    return {"total": sum(totals.values()), "by_status": totals,
            "by_rule": dict(sorted(by_rule.items(), key=lambda kv: (cleaning.CONFIDENCE_ORDER[kv[1]["confidence"]], kv[0])))}


def _row_dict(r):
    d = dict(r)
    d["column"] = d.pop("column_name")
    d["row"] = d.pop("row_no")
    d["rule_label"] = cleaning.RULES.get(d["rule"], d["rule"])
    return d


# ---- generate and review ---------------------------------------------------------------------------------
def generate(conn, user_id, dataset_id, storage):
    df = datasets.load_df(conn, user_id, dataset_id, storage)
    issues_file = datasets._path(storage, dataset_id, "_issues")
    if not issues_file.exists():
        datasets.run_validation(conn, user_id, dataset_id, storage)
    issues = pd.read_csv(issues_file, dtype=str, keep_default_na=False)
    props = cleaning.propose(df, issues)
    conn.execute("DELETE FROM corrections WHERE dataset_id = ?", (dataset_id,))
    conn.execute("DELETE FROM cleaning_runs WHERE dataset_id = ?", (dataset_id,))      # old results no longer match
    for suffix in ("_clean", "_changes", "_followup"):
        datasets._path(storage, dataset_id, suffix).unlink(missing_ok=True)
    conn.executemany(
        "INSERT INTO corrections (dataset_id, row_no, record_id, enumerator, column_name, column2, action, old_value, "
        "new_value, rule, confidence, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(dataset_id, int(p.row), str(p.record_id), str(p.enumerator), p.column, p.column2, p.action,
          str(p.old_value), str(p.new_value), p.rule, p.confidence, p.reason) for p in props.itertuples()])
    conn.commit()
    return _summary(conn, dataset_id)


def list_corrections(conn, user_id, dataset_id, status="", rule="", page=1, page_size=25):
    _check_dataset(conn, user_id, dataset_id)
    if status not in ("",) + STATUSES:
        raise ServiceError(422, "Status must be pending, accepted or rejected.")
    if rule and rule not in cleaning.RULES:
        raise ServiceError(422, "Unknown correction type.")
    where, args = ["dataset_id = ?"], [dataset_id]
    if status:
        where.append("status = ?")
        args.append(status)
    if rule:
        where.append("rule = ?")
        args.append(rule)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM corrections WHERE {clause}", args).fetchone()[0]
    page_size = min(max(int(page_size), 1), 100)
    pages = max(1, -(-total // page_size))
    page = min(max(int(page), 1), pages)
    rows = conn.execute(f"SELECT {FIELDS} FROM corrections WHERE {clause} ORDER BY row_no, id LIMIT ? OFFSET ?",
                        args + [page_size, (page - 1) * page_size]).fetchall()
    return {"summary": _summary(conn, dataset_id), "total": total, "page": page, "pages": pages,
            "rows": [_row_dict(r) for r in rows]}


def review(conn, user_id, dataset_id, status, ids=None, rule="", confidence=""):
    _check_dataset(conn, user_id, dataset_id)
    if status not in STATUSES:
        raise ServiceError(422, "Status must be pending, accepted or rejected.")
    if ids:
        if len(ids) > MAX_IDS:
            raise ServiceError(422, f"Choose at most {MAX_IDS} corrections at a time.")
        marks = ",".join("?" * len(ids))
        cur = conn.execute(f"UPDATE corrections SET status = ? WHERE dataset_id = ? AND id IN ({marks})",
                           [status, dataset_id] + [int(i) for i in ids])
    elif rule or confidence:
        if rule and rule not in cleaning.RULES:
            raise ServiceError(422, "Unknown correction type.")
        if confidence and confidence not in cleaning.CONFIDENCE_ORDER:
            raise ServiceError(422, "Confidence must be high, medium or low.")
        where, args = ["dataset_id = ?"], [dataset_id]
        if rule:
            where.append("rule = ?")
            args.append(rule)
        if confidence:
            where.append("confidence = ?")
            args.append(confidence)
        cur = conn.execute(f"UPDATE corrections SET status = ? WHERE {' AND '.join(where)}", [status] + args)
    else:
        raise ServiceError(422, "Choose which corrections to update.")
    conn.commit()
    return {"updated": cur.rowcount, "summary": _summary(conn, dataset_id)}


# ---- apply -----------------------------------------------------------------------------------------------
def apply(conn, user_id, dataset_id, storage):
    df = datasets.load_df(conn, user_id, dataset_id, storage)
    if conn.execute("SELECT COUNT(*) FROM corrections WHERE dataset_id = ?", (dataset_id,)).fetchone()[0] == 0:
        raise ServiceError(422, "Generate suggested corrections first.")
    rows = conn.execute(f"SELECT {FIELDS} FROM corrections WHERE dataset_id = ? AND status = 'accepted' ORDER BY row_no, id",
                        (dataset_id,)).fetchall()
    if not rows:
        raise ServiceError(422, "Accept at least one correction first.")
    accepted = []
    for r in rows:
        d = _row_dict(r)
        d["column2"] = d["column2"] or ""
        accepted.append(d)
    before = datasets.get_validation(conn, user_id, dataset_id)
    if not before.get("ran"):
        before = datasets.run_validation(conn, user_id, dataset_id, storage)

    clean, log, skipped = cleaning.apply_corrections(df, accepted)
    after = validate(clean)                                   # the index keeps the ORIGINAL row numbers
    followup = after["issues"][after["issues"].severity == "error"]
    clean.to_csv(datasets._path(storage, dataset_id, "_clean"), index=False)
    log.to_csv(datasets._path(storage, dataset_id, "_changes"), index=False)
    followup.to_csv(datasets._path(storage, dataset_id, "_followup"), index=False)

    b, a = before["summary"], after["summary"]
    report = jsonable({
        "ran": True, "records_before": len(df), "records_after": len(clean),
        "changes_applied": int((log.action != "delete_row").sum()), "records_removed": int((log.action == "delete_row").sum()),
        "skipped": skipped, "accepted": len(accepted),
        "score_before": b["quality_score"], "score_after": a["quality_score"],
        "errors_before": b["records_with_errors"], "errors_after": a["records_with_errors"],
        "completeness_before": b["completeness_pct"], "completeness_after": a["completeness_pct"],
        "followup_records": a["records_with_errors"], "followup_problems": len(followup)})
    conn.execute("INSERT OR REPLACE INTO cleaning_runs (dataset_id, report_json) VALUES (?, ?)", (dataset_id, json.dumps(report)))
    conn.commit()
    return report


def get_cleaning(conn, user_id, dataset_id):
    _check_dataset(conn, user_id, dataset_id)
    r = conn.execute("SELECT report_json, created_at FROM cleaning_runs WHERE dataset_id = ?", (dataset_id,)).fetchone()
    if not r:
        return {"ran": False}
    report = json.loads(r["report_json"])
    report["run_at"] = r["created_at"]
    return report


# ---- downloads -------------------------------------------------------------------------------------------
def export(conn, user_id, dataset_id, storage, what="cleaned", fmt="csv"):
    row = _check_dataset(conn, user_id, dataset_id)
    if what not in EXPORTS:
        raise ServiceError(422, "Choose original, cleaned, changes or followup.")
    if fmt not in ("csv", "xlsx"):
        raise ServiceError(422, "Format must be csv or xlsx.")
    path = datasets._path(storage, dataset_id, EXPORTS[what] or "")
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except OSError:
        raise ServiceError(404, "That file has not been created yet. Apply corrections first.")
    df = cleaning.safe_for_spreadsheet(df)
    stem = re.sub(r"\.[^.]+$", "", row["original_name"]) or "dataset"
    filename = f"{datasets.safe_name(stem)}_{EXPORT_NAMES[what]}.{fmt}"
    if fmt == "csv":
        return filename, "text/csv", df.to_csv(index=False).encode("utf-8-sig")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=EXPORT_NAMES[what][:31])
    return filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buf.getvalue()

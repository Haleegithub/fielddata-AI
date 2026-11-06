"""FieldData AI - Stage 5: dataset analytics and project summaries (business logic, no web framework)."""
import json

import pandas as pd

from fielddata import analytics
from fielddata.validation import validate

from . import datasets, services
from .services import ServiceError


def dataset_analytics(conn, user_id, dataset_id, storage, source="original"):
    row = datasets._row(conn, user_id, dataset_id)
    if source not in ("original", "cleaned"):
        raise ServiceError(422, "Source must be original or cleaned.")
    cleaned_ready = datasets._path(storage, dataset_id, "_clean").exists()
    if source == "cleaned":
        if not cleaned_ready:
            raise ServiceError(404, "There is no cleaned version yet. Apply corrections first.")
        df = pd.read_csv(datasets._path(storage, dataset_id, "_clean"), dtype=str, keep_default_na=False)
        issues = validate(df)["issues"]                       # fresh check, so row numbers match the cleaned table
    else:
        df = datasets.load_df(conn, user_id, dataset_id, storage)
        issues_file = datasets._path(storage, dataset_id, "_issues")
        if not issues_file.exists():
            datasets.run_validation(conn, user_id, dataset_id, storage)
        issues = pd.read_csv(issues_file, dtype=str, keep_default_na=False)
    result = analytics.analyse(df, issues)
    result.update({"source": source, "cleaned_available": cleaned_ready, "dataset": row["original_name"]})
    return result


def project_summary(conn, user_id, project_id):
    project = services.get_project(conn, user_id, project_id)
    rows = conn.execute(
        "SELECT d.id, d.original_name, d.n_rows, d.uploaded_at, v.score AS score, c.report_json AS clean_json "
        "FROM datasets d LEFT JOIN validation_runs v ON v.dataset_id = d.id LEFT JOIN cleaning_runs c ON c.dataset_id = d.id "
        "WHERE d.project_id = ? AND d.user_id = ? ORDER BY d.id", (project_id, user_id)).fetchall()
    items, scored_records, weighted = [], 0, 0.0
    for r in rows:
        clean = json.loads(r["clean_json"]) if r["clean_json"] else None
        items.append({"id": r["id"], "name": r["original_name"], "rows": r["n_rows"], "uploaded_at": r["uploaded_at"],
                      "quality_score": r["score"], "cleaned_score": clean["score_after"] if clean else None,
                      "records_after_cleaning": clean["records_after"] if clean else None,
                      "followup_records": clean["followup_records"] if clean else None})
        if r["score"] is not None:
            scored_records += r["n_rows"]
            weighted += r["score"] * r["n_rows"]
    return {"project": {"id": project["id"], "name": project["name"], "location": project["location"]},
            "datasets": items,
            "totals": {"datasets": len(items), "records": sum(i["rows"] for i in items),
                       "validated": sum(i["quality_score"] is not None for i in items),
                       "cleaned": sum(i["cleaned_score"] is not None for i in items),
                       "average_quality_score": round(weighted / scored_records, 1) if scored_records else None}}

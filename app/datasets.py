"""
FieldData AI - Stage 3: dataset upload, preview, column profile and validation (business logic).
Framework-free so it can be tested without a web server.
Uploaded files are re-saved under generated names (ds_<id>.csv); the user's file name is never used as a path.
"""
import csv
import io
import json
import re
import zipfile
from pathlib import Path

from openpyxl import load_workbook

import pandas as pd

from fielddata.validation import MISSING_TOKENS, detect_roles, jsonable, recommendations, validate

from . import services
from .services import ServiceError

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLS = 200
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
FILE_SUFFIXES = ("", "_issues", "_clean", "_changes", "_followup")


# ---- reading uploads -----------------------------------------------------------------------
def safe_name(filename):
    name = re.split(r"[\\/]", filename or "")[-1]
    name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", name).strip(" .")[:100]
    return name or "dataset"


def _decode(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_upload(filename, data):
    """Turn an uploaded CSV or .xlsx file into a table of text values. Raises ServiceError with a clear message."""
    name = safe_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in (".csv", ".xlsx"):
        raise ServiceError(415, "Only .csv and .xlsx files are supported.")
    if not data:
        raise ServiceError(422, "The file is empty.")
    if len(data) > MAX_BYTES:
        raise ServiceError(413, f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        if ext == ".csv":
            text = _decode(data)
            try:
                sep = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
            except csv.Error:
                sep = ","
            df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1)
        else:
            # XLSX is a ZIP container: reject extreme expansion ratios/sizes and stream
            # the first worksheet so row/column limits are enforced before loading all rows.
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise ServiceError(413, "The Excel file expands beyond the 100 MB processing limit.")
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                sheet = workbook.worksheets[0]
                iterator = sheet.iter_rows(values_only=True)
                header = next(iterator, None)
                if not header:
                    raise ServiceError(422, "The Excel file has no header row.")
                if len(header) > MAX_COLS:
                    raise ServiceError(422, f"Too many columns (limit {MAX_COLS} in this prototype).")
                records = []
                for row in iterator:
                    if len(records) >= MAX_ROWS:
                        raise ServiceError(422, f"Too many rows (limit {MAX_ROWS:,} in this prototype).")
                    records.append(["" if value is None else str(value) for value in row[:len(header)]])
                df = pd.DataFrame(records, columns=["" if h is None else str(h) for h in header])
            finally:
                workbook.close()
    except Exception:
        raise ServiceError(422, "The file could not be read. Check that it is a valid CSV or Excel (.xlsx) file.")
    if df.shape[1] == 0 or df.shape[0] == 0:
        raise ServiceError(422, "The file has no data rows.")
    if df.shape[1] > MAX_COLS:
        raise ServiceError(422, f"Too many columns (limit {MAX_COLS} in this prototype).")
    if df.shape[0] > MAX_ROWS:
        raise ServiceError(422, f"Too many rows (limit {MAX_ROWS:,} in this prototype).")
    df.columns = [str(c).strip() or f"column_{i + 1}" for i, c in enumerate(df.columns)]
    return df.astype(str)


# ---- storing and finding datasets -----------------------------------------------------------
def _path(storage, dataset_id, suffix=""):
    return Path(storage) / f"ds_{int(dataset_id)}{suffix}.csv"


def _row(conn, user_id, dataset_id):
    r = conn.execute("SELECT * FROM datasets WHERE id = ? AND user_id = ?", (dataset_id, user_id)).fetchone()
    if not r:
        raise ServiceError(404, "Dataset not found.")
    return r


def _summary(r):
    return {"id": r["id"], "project_id": r["project_id"], "name": r["original_name"], "rows": r["n_rows"],
            "columns": r["n_cols"], "size_bytes": r["size_bytes"], "uploaded_at": r["uploaded_at"]}


def save_dataset(conn, user_id, project_id, filename, data, storage):
    services.get_project(conn, user_id, project_id)                     # 404 unless it is the user's project
    df = parse_upload(filename, data)
    Path(storage).mkdir(parents=True, exist_ok=True)
    cur = conn.execute("INSERT INTO datasets (project_id, user_id, original_name, n_rows, n_cols, size_bytes) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (project_id, user_id, safe_name(filename), len(df), df.shape[1], len(data)))
    try:
        df.to_csv(_path(storage, cur.lastrowid), index=False)
    except OSError:
        conn.rollback()
        raise ServiceError(500, "The file could not be saved.")
    conn.commit()
    return _summary(_row(conn, user_id, cur.lastrowid))


def list_datasets(conn, user_id, project_id):
    services.get_project(conn, user_id, project_id)
    rows = conn.execute("SELECT * FROM datasets WHERE project_id = ? AND user_id = ? ORDER BY id DESC",
                        (project_id, user_id)).fetchall()
    return [_summary(r) for r in rows]


def load_df(conn, user_id, dataset_id, storage):
    _row(conn, user_id, dataset_id)
    try:
        return pd.read_csv(_path(storage, dataset_id), dtype=str, keep_default_na=False)
    except OSError:
        raise ServiceError(404, "The stored file is missing.")


def delete_dataset(conn, user_id, dataset_id, storage):
    _row(conn, user_id, dataset_id)
    for suffix in FILE_SUFFIXES:
        _path(storage, dataset_id, suffix).unlink(missing_ok=True)
    conn.execute("DELETE FROM datasets WHERE id = ? AND user_id = ?", (dataset_id, user_id))
    conn.commit()


def remove_project(conn, user_id, project_id, storage):
    """Delete a project together with its datasets and their files."""
    services.get_project(conn, user_id, project_id)
    for d in conn.execute("SELECT id FROM datasets WHERE project_id = ? AND user_id = ?", (project_id, user_id)).fetchall():
        for suffix in FILE_SUFFIXES:
            _path(storage, d["id"], suffix).unlink(missing_ok=True)
    services.delete_project(conn, user_id, project_id)


# ---- preview and column profile ----------------------------------------------------------------
def preview(conn, user_id, dataset_id, storage, offset=0, limit=20):
    df = load_df(conn, user_id, dataset_id, storage)
    offset, limit = max(int(offset), 0), min(max(int(limit), 1), 100)
    part = df.iloc[offset: offset + limit]
    return {"columns": list(df.columns), "rows": part.values.tolist(), "total": len(df), "offset": offset, "limit": limit}


def describe_columns(df):
    roles = detect_roles(list(df.columns))
    inv = {v: k for k, v in roles.items()}
    out = []
    for c in df.columns:
        s = df[c].str.strip()
        miss = s.str.lower().isin(MISSING_TOKENS)
        vals = s[~miss]
        if len(vals) == 0:
            kind = "empty"
        elif pd.to_numeric(vals, errors="coerce").notna().mean() >= 0.9:
            kind = "number"
        elif pd.to_datetime(vals.head(200), errors="coerce", format="mixed").notna().mean() >= 0.9:
            kind = "date"
        else:
            kind = "category" if vals.nunique() <= 20 else "text"
        out.append({"name": c, "role": inv.get(c, ""), "type": kind, "missing": int(miss.sum()),
                    "missing_pct": round(100 * float(miss.mean()), 1), "unique": int(vals.nunique()),
                    "examples": [str(v)[:40] for v in vals.drop_duplicates().head(3)]})
    return out


def dataset_info(conn, user_id, dataset_id, storage):
    r = _row(conn, user_id, dataset_id)
    df = load_df(conn, user_id, dataset_id, storage)
    info = _summary(r)
    info["column_details"] = describe_columns(df)
    return info


# ---- validation --------------------------------------------------------------------------------------
def run_validation(conn, user_id, dataset_id, storage):
    df = load_df(conn, user_id, dataset_id, storage)
    res = validate(df)
    res["issues"].to_csv(_path(storage, dataset_id, "_issues"), index=False)
    report = jsonable({
        "ran": True,
        "summary": res["summary"],
        "columns": res["columns"].reset_index(names="column").to_dict("records"),
        "enumerators": res["enumerators"].reset_index(names="enumerator").to_dict("records") if len(res["enumerators"]) else [],
        "recommendations": recommendations(res)})
    conn.execute("INSERT OR REPLACE INTO validation_runs (dataset_id, score, report_json) VALUES (?, ?, ?)",
                 (dataset_id, report["summary"]["quality_score"], json.dumps(report)))
    conn.commit()
    return report


def get_validation(conn, user_id, dataset_id):
    _row(conn, user_id, dataset_id)
    r = conn.execute("SELECT report_json, created_at FROM validation_runs WHERE dataset_id = ?", (dataset_id,)).fetchone()
    if not r:
        return {"ran": False}
    report = json.loads(r["report_json"])
    report["run_at"] = r["created_at"]
    return report


def get_issues(conn, user_id, dataset_id, storage, severity="", issue_type="", column="", page=1, page_size=50):
    _row(conn, user_id, dataset_id)
    if severity not in ("", "error", "warning"):
        raise ServiceError(422, "Severity must be 'error' or 'warning'.")
    try:
        iss = pd.read_csv(_path(storage, dataset_id, "_issues"), dtype=str, keep_default_na=False)
    except OSError:
        raise ServiceError(404, "Validation has not been run yet.")
    if severity:
        iss = iss[iss.severity == severity]
    if issue_type:
        iss = iss[iss.issue_type == issue_type]
    if column:
        iss = iss[iss["column"] == column]
    page_size = min(max(int(page_size), 1), 200)
    pages = max(1, -(-len(iss) // page_size))
    page = min(max(int(page), 1), pages)
    part = iss.iloc[(page - 1) * page_size: page * page_size]
    return {"total": len(iss), "page": page, "pages": pages, "columns": list(iss.columns), "rows": part.values.tolist()}

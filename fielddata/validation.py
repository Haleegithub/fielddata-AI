"""
FieldData AI - Stage 1: automated data validation engine.

Reads a survey table (everything as text, exactly as exported from KoboToolbox or Excel),
works out what each column means from its name, and finds:
missing values, duplicates, invalid entries, wrong formats, impossible or unusual values,
bad GPS coordinates, and inconsistent information.

Every issue says WHERE it is (row, column), WHAT is wrong, how serious it is, and what to do.
Portfolio prototype - rules are sensible defaults, to be agreed with the survey team.
"""
import re

import numpy as np
import pandas as pd

MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--"}
NIGERIA_BBOX = (4.0, 14.0, 2.5, 15.0)          # lat_min, lat_max, lon_min, lon_max

ROLE_PATTERNS = {                               # role -> regex on the lower-case column name
    "id": r"^(_?uuid|_?id|respondent_id|record_id)$",
    "submission_time": r"submission|^_?(start|end)$",
    "interview_date": r"date",
    "age": r"(^|_)age(_|$)",
    "gender": r"gender|(^|_)sex($|_)",
    "marital": r"marital",
    "household_size": r"household.*size|hh_?size",
    "children": r"children",
    "phone": r"phone|mobile|msisdn",
    "latitude": r"(^|_)lat(itude)?($|_)",
    "longitude": r"(^|_)(lon|lng|long|longitude)($|_)",
    "state": r"(^|_)state($|_)",
    "lga": r"(^|_)lga($|_)",
    "enumerator": r"enumerator|interviewer|field_?worker",
}
KEY_ROLES = {"id", "interview_date", "age", "gender", "latitude", "longitude", "state"}
GENDER_MAP = {"m": "Male", "male": "Male", "man": "Male", "f": "Female", "female": "Female", "woman": "Female"}
MARRIED_LIKE = {"married", "widowed", "divorced", "separated"}


def detect_roles(columns):
    roles = {}
    for role, pattern in ROLE_PATTERNS.items():
        for col in columns:
            if col not in roles.values() and re.search(pattern, re.sub(r"[^a-z0-9_]+", "_", col.lower())):
                roles[role] = col
                break
    return roles


def _haversine_km(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * np.arcsin(np.sqrt(a))


def validate(raw, roles=None, bbox=NIGERIA_BBOX, today=None, max_age=110, max_state_distance_km=150):
    """Validate a table of text values. Returns a dict with issues, summary and breakdowns."""
    if raw.shape[0] == 0 or raw.shape[1] == 0:
        raise ValueError("The file has no records to check.")
    df = raw.astype(str).apply(lambda s: s.str.strip())
    missing = df.apply(lambda s: s.str.lower().isin(MISSING_TOKENS))
    roles = roles or detect_roles(list(df.columns))
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.today().normalize()
    col = lambda r: roles.get(r)
    idcol, enumcol = col("id"), col("enumerator")
    issues = []

    def add(ix, column, itype, severity, message, suggestion, value=None):
        issues.append({
            "row": int(ix) + 2, "record_id": df.at[ix, idcol] if idcol else int(ix) + 2,
            "enumerator": df.at[ix, enumcol] if enumcol else "",
            "column": column, "issue_type": itype, "severity": severity,
            "value": df.at[ix, column] if value is None and column in df.columns else value,
            "message": message, "suggestion": suggestion})

    # 1. missing values --------------------------------------------------------------------
    key_cols = {roles[r] for r in KEY_ROLES if r in roles}
    for c in df.columns:
        sev = "error" if c in key_cols else "warning"
        for ix in df.index[missing[c]]:
            add(ix, c, "missing_value", sev, f"'{c}' is blank",
                "Ask the enumerator to revisit or confirm the answer" if sev == "error" else "Check if the question was skipped")

    # 2. duplicates ------------------------------------------------------------------------
    if idcol:
        d = df[idcol].duplicated(keep="first") & ~missing[idcol]
        for ix in df.index[d]:
            add(ix, idcol, "duplicate_record", "error", "This ID appears more than once", "Keep one record and delete the other")
    skip = {c for c in df.columns if c.startswith("_")} | {roles[r] for r in ("id", "submission_time") if r in roles}
    content = [c for c in df.columns if c not in skip]
    if content:
        d = df.duplicated(subset=content, keep="first") & ~missing[content].all(axis=1)
        for ix in df.index[d]:
            add(ix, "*", "duplicate_record", "error", "Identical to an earlier record (same answers, different submission)",
                "Check if the respondent was interviewed twice; keep one", value="")

    # 3. age ---------------------------------------------------------------------------------
    age_n = None
    if col("age"):
        c = col("age")
        age_n = pd.to_numeric(df[c].where(~missing[c]), errors="coerce")
        for ix in df.index[~missing[c] & age_n.isna()]:
            add(ix, c, "format_error", "error", "Age is not a number", "Enter age in years as a number")
        for ix in df.index[age_n.notna() & ((age_n < 0) | (age_n > max_age))]:
            add(ix, c, "impossible_value", "error", f"Age {df.at[ix, c]} is not possible", f"Age must be between 0 and {max_age}; confirm with respondent")

    # 4. gender --------------------------------------------------------------------------------
    if col("gender"):
        c = col("gender")
        for ix in df.index[~missing[c]]:
            v = df.at[ix, c]
            std = GENDER_MAP.get(v.lower())
            if std is None:
                add(ix, c, "invalid_value", "error", f"'{v}' is not a valid gender entry", "Use Male or Female (add other options in the settings if your form allows them)")
            elif v != std:
                add(ix, c, "inconsistent_category", "warning", f"'{v}' is written differently from the standard '{std}'", f"Standardise to '{std}'")

    # 5. dates ----------------------------------------------------------------------------------
    for role in ("interview_date", "submission_time"):
        if col(role):
            c = col(role)
            parsed = pd.to_datetime(df[c].where(~missing[c]), errors="coerce", format="mixed")
            for ix in df.index[~missing[c] & parsed.isna()]:
                add(ix, c, "format_error", "error", "Date could not be read", "Use the format YYYY-MM-DD")
            for ix in df.index[parsed > today]:
                add(ix, c, "impossible_value", "error", "Date is in the future", "Check the tablet clock or correct the date")

    # 6. phone ----------------------------------------------------------------------------------
    if col("phone"):
        c = col("phone")
        for ix in df.index[~missing[c]]:
            digits = re.sub(r"[\s+\-()]", "", df.at[ix, c])
            if not re.fullmatch(r"0[789][01]\d{8}|234[789][01]\d{8}", digits):
                add(ix, c, "format_error", "warning", "Phone number does not look like a valid Nigerian number", "Confirm the number with the respondent")

    # 7. household size and children --------------------------------------------------------------
    hh_n = ch_n = None
    if col("household_size"):
        c = col("household_size")
        hh_n = pd.to_numeric(df[c].where(~missing[c]), errors="coerce")
        for ix in df.index[~missing[c] & hh_n.isna()]:
            add(ix, c, "format_error", "error", "Household size is not a number", "Enter a whole number")
        for ix in df.index[hh_n < 1]:
            add(ix, c, "impossible_value", "error", "Household size must be at least 1", "Correct the household size")
        for ix in df.index[hh_n > 25]:
            add(ix, c, "unusual_value", "warning", f"Household size {df.at[ix, c]} is unusually large", "Confirm with the respondent")
    if col("children"):
        c = col("children")
        ch_n = pd.to_numeric(df[c].where(~missing[c]), errors="coerce")
        for ix in df.index[ch_n < 0]:
            add(ix, c, "impossible_value", "error", "Number of children cannot be negative", "Correct the entry")

    # 8. unusual numbers in other numeric columns (3 x IQR rule) ------------------------------------
    handled = {roles[r] for r in ("age", "household_size", "children", "latitude", "longitude", "phone") if r in roles}
    for c in df.columns:
        if c in handled or c in skip:
            continue
        num = pd.to_numeric(df[c].where(~missing[c]), errors="coerce")
        if num.notna().mean() > 0.9 and num.nunique() > 15:
            q1, q3 = num.quantile(.25), num.quantile(.75)
            lo, hi = q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)
            for ix in df.index[(num < lo) | (num > hi)]:
                add(ix, c, "unusual_value", "warning", f"{df.at[ix, c]} is far outside the usual range ({lo:.0f} to {hi:.0f})", "Check for a typing error (extra zero?)")

    # 9. GPS ------------------------------------------------------------------------------------------
    if col("latitude") and col("longitude"):
        la, lo = col("latitude"), col("longitude")
        lat = pd.to_numeric(df[la].where(~missing[la]), errors="coerce")
        lon = pd.to_numeric(df[lo].where(~missing[lo]), errors="coerce")
        bad_text = (~missing[la] & lat.isna()) | (~missing[lo] & lon.isna())
        for ix in df.index[bad_text]:
            add(ix, la, "format_error", "error", "GPS coordinate is not a number", "Recapture the location", value=f"{df.at[ix, la]}, {df.at[ix, lo]}")
        have = lat.notna() & lon.notna()
        for ix in df.index[missing[la] ^ missing[lo]]:
            add(ix, la, "invalid_gps", "error", "Only one of latitude / longitude is filled in", "Recapture the location")
        rng_bad = have & ((lat.abs() > 90) | (lon.abs() > 180))
        zero = have & (lat.abs() < 0.01) & (lon.abs() < 0.01)
        outside = have & ~rng_bad & ~zero & ~((lat.between(bbox[0], bbox[1])) & (lon.between(bbox[2], bbox[3])))
        swapped = outside & lon.between(bbox[0], bbox[1]) & lat.between(bbox[2], bbox[3])
        for ix in df.index[rng_bad]:
            add(ix, la, "invalid_gps", "error", "Coordinates are outside the valid range (lat -90..90, lon -180..180)", "Recapture the location", value=f"{lat[ix]}, {lon[ix]}")
        for ix in df.index[zero]:
            add(ix, la, "invalid_gps", "error", "Coordinates are 0,0 (GPS did not capture a location)", "Recapture the location outdoors", value="0, 0")
        for ix in df.index[swapped]:
            add(ix, la, "invalid_gps", "error", "Latitude and longitude look swapped", "Swap the two values", value=f"{lat[ix]}, {lon[ix]}")
        for ix in df.index[outside & ~swapped]:
            add(ix, la, "invalid_gps", "error", "Location is outside the expected survey country", "Recapture the location", value=f"{lat[ix]}, {lon[ix]}")
        if col("state"):                                      # point far from the rest of its state
            st = df[col("state")].where(~missing[col("state")])
            ok = have & ~rng_bad & ~zero & ~outside
            med = pd.DataFrame({"s": st[ok], "lat": lat[ok], "lon": lon[ok]}).groupby("s").agg(
                n=("lat", "size"), lat=("lat", "median"), lon=("lon", "median"))
            med = med[med.n >= 10]
            for ix in df.index[ok & st.isin(med.index)]:
                m = med.loc[st[ix]]
                dist = _haversine_km(lat[ix], lon[ix], m.lat, m.lon)
                swap_dist = _haversine_km(lon[ix], lat[ix], m.lat, m.lon) if abs(lon[ix]) <= 90 else np.inf
                if dist > 50 and swap_dist < 60 and swap_dist < 0.4 * dist:   # swapping puts it back in its state
                    add(ix, la, "invalid_gps", "error", "Latitude and longitude look swapped (swapping puts the point inside its own state)",
                        "Swap the two values", value=f"{lat[ix]}, {lon[ix]}")
                elif dist > max_state_distance_km:
                    add(ix, la, "gps_state_mismatch", "error", f"Location is about {dist:.0f} km from the other {st[ix]} records",
                        f"Check state and GPS; one of them may be wrong", value=f"{lat[ix]}, {lon[ix]}")

    # 10. inconsistent information -----------------------------------------------------------------------
    if age_n is not None and col("marital"):
        c = col("marital")
        for ix in df.index[(age_n < 15) & df[c].str.lower().isin(MARRIED_LIKE)]:
            add(ix, c, "inconsistent_info", "error", f"Age {df.at[ix, col('age')]} but marital status is '{df.at[ix, c]}'", "Check age and marital status", value=df.at[ix, c])
    if hh_n is not None and ch_n is not None:
        for ix in df.index[(ch_n > hh_n) & hh_n.notna()]:
            add(ix, col("children"), "inconsistent_info", "error", f"{df.at[ix, col('children')]} children but household size is {df.at[ix, col('household_size')]}", "Check both numbers")
    if col("state") and col("lga"):
        s, l = col("state"), col("lga")
        ok = ~missing[s] & ~missing[l]
        counts = df[ok].groupby([l, s]).size().reset_index(name="n")
        top = counts.sort_values("n", ascending=False).drop_duplicates(l).set_index(l)
        totals = counts.groupby(l).n.sum()
        for ix in df.index[ok]:
            lg = df.at[ix, l]
            if totals[lg] >= 5 and top.at[lg, s] != df.at[ix, s]:
                add(ix, l, "inconsistent_info", "warning", f"LGA '{lg}' is usually in {top.at[lg, s]}, but the state is '{df.at[ix, s]}'", "Check state and LGA", value=f"{lg} / {df.at[ix, s]}")

    return _summarise(df, missing, issues, roles, enumcol)


def _summarise(df, missing, issues, roles, enumcol):
    cols = ["row", "record_id", "enumerator", "column", "issue_type", "severity", "value", "message", "suggestion"]
    iss = pd.DataFrame(issues, columns=cols)
    n = len(df)
    err_rows = set(iss.loc[iss.severity == "error", "row"])
    warn_rows = set(iss.loc[iss.severity == "warning", "row"]) - err_rows
    summary = {
        "records": n, "columns": df.shape[1],
        "records_with_errors": len(err_rows), "records_with_warnings_only": len(warn_rows),
        "clean_records": n - len(err_rows) - len(warn_rows),
        "quality_score": round(100 * (1 - len(err_rows) / n), 1) if n else 0.0,
        "completeness_pct": round(100 * (1 - missing.values.mean()), 1),
        "issues_total": len(iss), "issues_by_type": iss.issue_type.value_counts().to_dict(),
        "issues_by_severity": iss.severity.value_counts().to_dict(),
        "roles_detected": roles}
    inv = {v: k for k, v in roles.items()}
    colsum = pd.DataFrame({
        "role": [inv.get(c, "") for c in df.columns],
        "missing": missing.sum().values, "missing_pct": (missing.mean() * 100).round(1).values,
        "unique_values": [df[c].nunique() for c in df.columns],
        "issues": [int((iss.column == c).sum()) for c in df.columns]}, index=df.columns).sort_values("issues", ascending=False)
    enum = pd.DataFrame()
    if enumcol:
        rec = df[enumcol].value_counts().rename("records")
        err = iss[iss.severity == "error"].groupby("enumerator").row.nunique().rename("records_with_errors")
        top = iss[iss.severity == "error"].groupby("enumerator").issue_type.agg(lambda s: s.value_counts().index[0]).rename("most_common_problem")
        enum = pd.concat([rec, err, top], axis=1).fillna({"records_with_errors": 0, "most_common_problem": ""})
        enum["records_with_errors"] = enum.records_with_errors.astype(int)
        enum["error_rate_pct"] = (100 * enum.records_with_errors / enum.records).round(1)
        enum = enum.sort_values("error_rate_pct", ascending=False)
    return {"issues": iss, "summary": summary, "columns": colsum, "enumerators": enum}


def recommendations(result):
    """Plain-language advice based on what was actually found (no invented numbers)."""
    s, iss, enum = result["summary"], result["issues"], result["enumerators"]
    n, out = max(s["records"], 1), []
    by = s["issues_by_type"]
    if by.get("invalid_gps", 0) + by.get("gps_state_mismatch", 0) >= 0.02 * n:
        out.append("GPS problems are common: add a minimum-accuracy check and a required GPS capture to the form, and ask enumerators to capture the location outdoors.")
    if by.get("duplicate_record", 0):
        out.append(f"{by['duplicate_record']} duplicate records found: add a duplicate check (for example on phone number or household ID) and review the duplicates before analysis.")
    if by.get("inconsistent_category", 0) or by.get("invalid_value", 0):
        out.append("Free-text or inconsistent entries found: use fixed answer choices (select-one lists) for categories such as gender.")
    if by.get("missing_value", 0) >= 0.02 * n:
        top = result["columns"].sort_values("missing", ascending=False).index[0]
        out.append(f"Many blanks: make key questions required in the form. '{top}' has the most blanks.")
    if by.get("format_error", 0) or by.get("impossible_value", 0):
        out.append("Impossible or badly formatted values found: add constraints in the form (for example age 0 to 110, valid date range) so they cannot be saved.")
    if len(enum) >= 3:
        avg = enum.records_with_errors.sum() / enum.records.sum() * 100
        high = enum[(enum.error_rate_pct > 1.5 * avg) & (enum.records >= 10)]
        if len(high):
            ids = ", ".join(high.index[:5])
            out.append(f"Enumerators with a much higher error rate than average: {ids}. Offer them refresher training and check their recent submissions first.")
    if not out:
        out.append("No major problems found. Keep monitoring new submissions.")
    return out
from datetime import date, datetime
from pathlib import Path

def jsonable(value):
    """Convert common Python objects into JSON-compatible values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {
            str(k): jsonable(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]

    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())

    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass

    return value
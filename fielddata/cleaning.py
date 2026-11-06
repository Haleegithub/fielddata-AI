"""
FieldData AI - Stage 4: suggested corrections and how they are applied.

Principles
- Nothing is changed automatically. The engine only PROPOSES corrections; a person accepts or rejects each one.
- The original upload is never overwritten. Accepted corrections produce a separate cleaned copy plus a change log.
- Only fixes that can be justified from the data itself are proposed. Missing values are never invented;
  impossible values can only be cleared, and the record goes on a follow-up list for the enumerator.
"""
import re

import pandas as pd

from .validation import GENDER_MAP, detect_roles

RULES = {
    "standardise_category": "Standardise the spelling of a category",
    "swap_gps": "Swap latitude and longitude",
    "remove_duplicate": "Remove a duplicate record",
    "number_words_to_digits": "Convert an age written in words",
    "clear_invalid_value": "Clear an invalid value (record needs follow-up)",
    "trim_whitespace": "Remove extra spaces",
}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
CLEARABLE = {"impossible_value", "invalid_value", "format_error", "invalid_gps"}
COLUMNS = ["row", "record_id", "enumerator", "column", "column2", "action", "old_value", "new_value",
           "rule", "confidence", "reason"]

_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def words_to_number(text):
    """'thirty five' / 'forty-two' / 'seventeen' -> number, otherwise None."""
    parts = re.sub(r"[-\s]+", " ", str(text).strip().lower()).split()
    if len(parts) == 1:
        return _ONES.get(parts[0], _TENS.get(parts[0]))
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES and 1 <= _ONES[parts[1]] <= 9:
        return _TENS[parts[0]] + _ONES[parts[1]]
    return None


def propose(df, issues, roles=None):
    """Suggest corrections for the problems found by validation. df = original text table, issues = validation issues."""
    roles = roles or detect_roles(list(df.columns))
    idcol, enumcol = roles.get("id"), roles.get("enumerator")
    out, taken = [], set()
    iss = issues.copy()
    iss["row"] = iss["row"].astype(int)

    def add(row, col, action, old, new, rule, conf, reason, col2=""):
        keys = {(row, col)} | ({(row, col2)} if col2 else set())
        if keys & taken:
            return
        taken.update(keys)
        ix = row - 2
        out.append({"row": row, "record_id": df.at[ix, idcol] if idcol else row,
                    "enumerator": df.at[ix, enumcol] if enumcol else "", "column": col, "column2": col2,
                    "action": action, "old_value": old, "new_value": new, "rule": rule,
                    "confidence": conf, "reason": reason})

    # 1. duplicates: remove the later copy
    for r in iss[iss.issue_type == "duplicate_record"].itertuples():
        add(r.row, "*", "delete_row", "(whole record)", "", "remove_duplicate", "medium", r.message)
    deleted = {p["row"] for p in out}

    for r in iss[~iss.row.isin(deleted)].itertuples():
        ix = r.row - 2
        # 2. category spelling (gender)
        if r.issue_type == "inconsistent_category" and r.column == roles.get("gender"):
            std = GENDER_MAP.get(str(df.at[ix, r.column]).strip().lower())
            if std:
                add(r.row, r.column, "replace", df.at[ix, r.column], std, "standardise_category", "high", r.message)
        # 3. swapped GPS
        elif r.issue_type == "invalid_gps" and "swapped" in r.message.lower() and roles.get("longitude"):
            la, lo = r.column, roles["longitude"]
            lat, lon = df.at[ix, la], df.at[ix, lo]
            add(r.row, la, "swap", f"{lat}, {lon}", f"{lon}, {lat}", "swap_gps", "high", r.message, col2=lo)
        # 4. age written in words
        elif r.issue_type == "format_error" and r.column == roles.get("age"):
            n = words_to_number(df.at[ix, r.column])
            if n is not None and 0 <= n <= 110:
                add(r.row, r.column, "replace", df.at[ix, r.column], str(n), "number_words_to_digits", "medium",
                    f"'{df.at[ix, r.column]}' read as {n}")

    # 5. values that cannot be corrected from the data: clear them and send the record for follow-up
    for r in iss[(iss.severity == "error") & iss.issue_type.isin(CLEARABLE) & ~iss.row.isin(deleted)].itertuples():
        ix = r.row - 2
        if r.column == roles.get("latitude") and roles.get("longitude"):
            lo = roles["longitude"]
            add(r.row, r.column, "clear", f"{df.at[ix, r.column]}, {df.at[ix, lo]}", "", "clear_invalid_value", "low",
                r.message + ". Needs the enumerator to recapture it", col2=lo)
        elif r.column in df.columns:
            add(r.row, r.column, "clear", df.at[ix, r.column], "", "clear_invalid_value", "low",
                r.message + ". Needs the enumerator to confirm it")

    # 6. stray spaces in any other cell
    stripped = df.apply(lambda s: s.str.strip())
    changed = (df != stripped) & (stripped != "")
    for col in df.columns:
        for ix in df.index[changed[col]]:
            if ix + 2 not in deleted:
                add(ix + 2, col, "replace", df.at[ix, col], stripped.at[ix, col], "trim_whitespace", "high",
                    "Extra spaces before or after the value")

    res = pd.DataFrame(out, columns=COLUMNS)
    if len(res):
        res = res.sort_values(["row", "column"], kind="stable").reset_index(drop=True)
    return res


def apply_corrections(df, accepted):
    """Apply accepted proposals (list of dicts) to a COPY of df. The index keeps the original row positions.
    Returns (cleaned table, change log, number skipped). A proposal is skipped if the cell no longer holds the old value."""
    clean, log, skipped = df.copy(), [], 0
    props = [dict(p) for p in accepted]
    deleted = {p["row"] for p in props if p["action"] == "delete_row"}

    def note(p, col, old, new):
        log.append({"row": p["row"], "record_id": p["record_id"], "column": col, "action": p["action"],
                    "old_value": old, "new_value": new, "rule": p["rule"], "confidence": p["confidence"], "reason": p["reason"]})

    for p in props:
        ix = p["row"] - 2
        if ix not in clean.index:
            skipped += 1
            continue
        if p["action"] == "delete_row" or p["row"] in deleted:
            continue                                     # the whole record is removed, so cell fixes are pointless
        col, col2 = p["column"], p.get("column2") or ""
        if p["action"] == "replace":
            if str(clean.at[ix, col]) != str(p["old_value"]):
                skipped += 1
                continue
            clean.at[ix, col] = p["new_value"]
            note(p, col, p["old_value"], p["new_value"])
        elif p["action"] == "swap":
            a, b = clean.at[ix, col], clean.at[ix, col2]
            if f"{a}, {b}" != str(p["old_value"]):
                skipped += 1
                continue
            clean.at[ix, col], clean.at[ix, col2] = b, a
            note(p, f"{col} / {col2}", p["old_value"], p["new_value"])
        elif p["action"] == "clear":
            cols = [c for c in (col, col2) if c]
            if ", ".join(str(clean.at[ix, c]) for c in cols) != str(p["old_value"]):
                skipped += 1
                continue
            for c in cols:
                clean.at[ix, c] = ""
            note(p, " / ".join(cols), p["old_value"], "")
        else:
            skipped += 1
    for p in props:
        if p["action"] == "delete_row" and p["row"] - 2 in clean.index:
            clean = clean.drop(index=p["row"] - 2)
            note(p, "*", "(whole record)", "(removed)")
    return clean, pd.DataFrame(log, columns=["row", "record_id", "column", "action", "old_value", "new_value",
                                             "rule", "confidence", "reason"]), skipped


# ---- safe export for Excel / Google Sheets -------------------------------------------------------------
_NUMBER_LIKE = re.compile(r"^[+-]?[\d\s().,-]*$")


def safe_cell(value):
    """Stop spreadsheet programs from running text such as =HYPERLINK(...) that came from the survey data."""
    if not isinstance(value, str) or not value:
        return value
    first = value[0]
    if first in "=@\t\r" or (first in "+-" and not _NUMBER_LIKE.match(value)):
        return "'" + value
    return value


def safe_for_spreadsheet(df):
    return df.apply(lambda s: s.map(safe_cell))

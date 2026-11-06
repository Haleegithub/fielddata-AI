"""
FieldData AI - Stage 4: are the suggested corrections actually right?
Accepts every high- and medium-confidence suggestion on the sample survey, then compares the result
with the TRUE values that were recorded when the errors were planted (planted_errors.csv).
Run:  python evaluate_cleaning.py
"""
import json

import pandas as pd

from fielddata import cleaning
from fielddata.validation import validate

raw = pd.read_csv("sample_data/survey_sample.csv", dtype=str, keep_default_na=False)
planted = pd.read_csv("sample_data/planted_errors.csv", dtype=str, keep_default_na=False)
res = validate(raw)
props = cleaning.propose(raw, res["issues"])
accepted = props[props.confidence.isin(["high", "medium"])]
clean, log, skipped = cleaning.apply_corrections(raw, accepted.to_dict("records"))
ix_of = dict(zip(raw["_id"], raw.index))

rows = []
for ptype in ["gender_variant", "gps_swapped", "age_text", "duplicate_record"]:
    group = planted[planted.planted_type == ptype]
    ok = wrong = left = 0
    for _, p in group.iterrows():
        truth, i = json.loads(p.original), ix_of[p.record_id]
        if ptype == "duplicate_record":
            ok += i not in clean.index
            left += i in clean.index
            continue
        if i not in clean.index:
            wrong += 1
            continue
        cols = list(truth)
        changed = any(clean.at[i, c] != raw.at[i, c] for c in cols)
        if not changed:
            left += 1
        elif all(clean.at[i, c] == truth[c] for c in cols):
            ok += 1
        else:
            wrong += 1
    rows.append((ptype, len(group), ok, wrong, left))
tbl = pd.DataFrame(rows, columns=["planted_error", "planted", "fixed_correctly", "fixed_wrongly", "left_for_follow_up"]).set_index("planted_error")
print("Suggested corrections (high + medium confidence accepted) compared with the true values:\n")
print(tbl.to_string())

planted_ids = set(planted.record_id)
touched = set(log.record_id.astype(str))
wrong_records = touched - planted_ids
print(f"\nRecords changed or removed that had NO planted error: {len(wrong_records)}")
print(f"Proposals skipped because the cell had changed: {skipped}")

after = validate(clean)
print(f"\nQuality score before: {res['summary']['quality_score']}%   after: {after['summary']['quality_score']}%")
print(f"Records with errors before: {res['summary']['records_with_errors']}   after: {after['summary']['records_with_errors']}")
low = props[props.confidence == "low"]
print(f"\nLow-confidence suggestions (clear an invalid value, not accepted here): {len(low)}")
print("Records still needing follow-up after cleaning (cannot be fixed from the data):", after["summary"]["records_with_errors"])
print("\nNote: the sample errors were planted by the same author who wrote the rules, so this is an easy test.")

"""
FieldData AI - Stage 1: how good is the validator?
Compares what it found with the errors that were deliberately planted in the sample survey.
Recall = of the planted errors, how many were caught.
False alarms = records flagged with an ERROR although nothing was planted in them.
"""
from pathlib import Path

import pandas as pd

from fielddata.validation import validate

raw = pd.read_csv("sample_data/survey_sample.csv", dtype=str, keep_default_na=False)
planted = pd.read_csv("sample_data/planted_errors.csv", dtype={"record_id": str})
res = validate(raw)
iss = res["issues"]
iss["record_id"] = iss["record_id"].astype(str)

rows = []
for _, p in planted.iterrows():
    mine = iss[iss.record_id == p.record_id]
    if p.planted_type == "duplicate_record":
        hit = (mine.issue_type == "duplicate_record").any()
    else:
        cols = set(p.columns.split("|"))
        hit = mine.column.isin(cols).any()
    rows.append((p.planted_type, hit))
r = pd.DataFrame(rows, columns=["planted_type", "caught"])
table = r.groupby("planted_type").caught.agg(planted="size", caught="sum")
table["recall_pct"] = (100 * table.caught / table.planted).round(0).astype(int)
print(table.sort_values("recall_pct").to_string())
print(f"\nOVERALL: caught {int(r.caught.sum())} of {len(r)} planted errors ({100 * r.caught.mean():.1f}%)")

err_records = set(iss.loc[iss.severity == "error", "record_id"])
planted_records = set(planted.record_id)
false_alarm = err_records - planted_records
print(f"\nFalse alarms: {len(false_alarm)} records flagged with an error that had nothing planted")
print(f"  (out of {len(raw) - len(planted_records)} records with no planted error)")
if false_alarm:
    print(iss[(iss.record_id.isin(false_alarm)) & (iss.severity == "error")][["record_id", "column", "issue_type", "message"]].head(8).to_string(index=False))
Path("output").mkdir(exist_ok=True)
table.to_csv("output/validation_evaluation.csv")

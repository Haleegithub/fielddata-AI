"""
FieldData AI - Stage 1: validate a survey file and print a data quality report.
Usage:  python run_validation.py                      (uses the sample survey)
        python run_validation.py my_data.csv          (CSV or Excel .xlsx)
"""
import json
import sys
from pathlib import Path

import pandas as pd

from fielddata.validation import recommendations, validate

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample_data/survey_sample.csv")
if path.suffix.lower() in (".xlsx", ".xls"):
    raw = pd.read_excel(path, dtype=str, keep_default_na=False)
else:
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)

res = validate(raw)
s = res["summary"]
Path("output").mkdir(exist_ok=True)
res["issues"].to_csv("output/validation_issues.csv", index=False)
res["columns"].to_csv("output/column_summary.csv")
res["enumerators"].to_csv("output/enumerator_summary.csv")
Path("output/quality_summary.json").write_text(json.dumps(s, indent=2, default=str))

print(f"FILE: {path.name}   ({s['records']} records, {s['columns']} columns)\n")
print(f"DATA QUALITY SCORE: {s['quality_score']}%  (records with no errors)")
print(f"  Records with errors: {s['records_with_errors']}   warnings only: {s['records_with_warnings_only']}   fully clean: {s['clean_records']}")
print(f"  Completeness: {s['completeness_pct']}% of cells filled\n")
print("Issues by type:")
print(res["issues"].issue_type.value_counts().to_string())
print("\nColumns with the most issues:")
print(res["columns"].head(6)[["role", "missing", "issues"]].to_string())
if len(res["enumerators"]):
    print("\nEnumerators with the highest error rate:")
    print(res["enumerators"].head(4).to_string())
print("\nRecommendations:")
for r in recommendations(res):
    print("  -", r)
print("\nSaved in the output folder: validation_issues.csv, column_summary.csv, enumerator_summary.csv, quality_summary.json")

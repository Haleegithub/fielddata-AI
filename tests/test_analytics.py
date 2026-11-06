"""Tests for analytics. Run:  python -m unittest discover tests -v"""
import json
import unittest
from pathlib import Path

import pandas as pd

from fielddata import analytics
from fielddata.validation import validate

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "survey_sample.csv"


class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
        cls.issues = validate(cls.raw)["issues"]
        cls.a = analytics.analyse(cls.raw, cls.issues)

    def test_overview_matches_validation(self):
        o = self.a["overview"]
        self.assertEqual(o["records_total"], 615)
        self.assertEqual(o["duplicates_excluded"], 15)
        self.assertEqual(o["records_used"], 600)
        self.assertEqual(o["records_with_errors"], 165)
        self.assertEqual((o["enumerators"], o["states"]), (12, 6))
        self.assertGreater(o["cells_excluded"], 100)
        self.assertEqual(o["date_from"] <= o["date_to"], True)

    def test_impossible_values_are_left_out_of_statistics(self):
        age = self.a["numeric"]["respondent_age"]
        self.assertLessEqual(age["max"], 110)                       # 150, 250 and 999 were excluded
        self.assertGreaterEqual(age["min"], 8)                      # negative ages excluded
        hh = self.a["numeric"]["household_size"]
        self.assertGreaterEqual(hh["min"], 1)                       # a household of 0 is an error, so it is excluded
        self.assertGreater(hh["max"], 25)                           # 60 and 85 are only WARNINGS, so they are kept...
        self.assertGreater(self.a["overview"]["unusual_values_kept"], 0)   # ...and reported
        self.assertLessEqual(hh["median"], 12)                      # the median is not distorted by them
        self.assertEqual(sum(c for _, c in age["histogram"]), age["n"])

    def test_every_planted_error_stays_out_of_the_numbers(self):
        planted = pd.read_csv(SAMPLE.parent / "planted_errors.csv", dtype=str, keep_default_na=False)
        ids = set(planted[planted.planted_type.isin(["age_too_high", "age_negative"])].record_id)
        bad = self.raw[self.raw._id.isin(ids)].respondent_age.astype(float)
        self.assertTrue(((bad < 0) | (bad > 110)).all())
        kept = pd.to_numeric(self.raw.respondent_age, errors="coerce")
        self.assertGreater(((kept < 0) | (kept > 110)).sum(), 0)    # they exist in the raw table...
        age = self.a["numeric"]["respondent_age"]
        self.assertTrue(0 <= age["min"] and age["max"] <= 110)      # ...but not in the analysis

    def test_categories(self):
        g = self.a["categorical"]["gender"]
        self.assertTrue({"Male", "Female"} <= {v[0] for v in g["values"]})
        self.assertAlmostEqual(sum(v[2] for v in g["values"]), 100, delta=0.5)
        self.assertNotIn("_id", self.a["categorical"])
        self.assertNotIn("phone_number", self.a["categorical"])

    def test_trend(self):
        t = self.a["trend"]
        self.assertEqual(t["column"], "interview_date")
        total = sum(c for _, c in t["daily"])
        self.assertLessEqual(total, self.a["overview"]["records_used"])
        self.assertGreater(total, self.a["overview"]["records_used"] - 25)      # only unreadable or future dates are missing
        self.assertEqual(t["daily"][0][0], self.a["overview"]["date_from"])
        self.assertLessEqual(t["peak_count"], max(c for _, c in t["daily"]))
        self.assertFalse(any(d.startswith("2027") for d, _ in t["daily"]))   # future dates excluded

    def test_geography(self):
        g = self.a["geography"]
        states = {v[0] for v in g["by_state"]["values"]}
        self.assertEqual(states, {"Kano", "Kaduna", "FCT", "Lagos", "Oyo", "Enugu"})
        self.assertLess(g["gps_coverage_pct"], 100)                 # blank and invalid GPS are not counted
        self.assertGreater(g["gps_coverage_pct"], 80)
        self.assertEqual(len(g["top_lgas"]), 15)

    def test_quality_indicators(self):
        q = self.a["quality"]
        self.assertEqual(q["missing_by_column"][0][0], "respondent_age")
        self.assertEqual(q["missing_by_column"][0][1], 20)
        worst = q["by_enumerator"][0]
        self.assertEqual(len(worst), 4)
        rates = [r[3] for r in q["by_enumerator"]]
        self.assertEqual(rates, sorted(rates, reverse=True))
        self.assertEqual(sum(r[1] for r in q["by_enumerator"]), 615)

    def test_findings_are_grounded_sentences(self):
        f = self.a["findings"]
        self.assertGreaterEqual(len(f), 6)
        self.assertIn("615", f[0])
        self.assertTrue(any("highest error rate" in x for x in f))
        self.assertTrue(any("Median respondent age" in x for x in f))
        top = self.a["geography"]["by_state"]["values"][0]
        self.assertTrue(any(top[0] in x and f"{top[1]:,}" in x for x in f))

    def test_json_safe(self):
        json.dumps(self.a)

    def test_handles_unknown_columns_and_tiny_tables(self):
        df = pd.DataFrame({"colour": ["red", "blue", "red"], "size": ["1", "2", "3"]})
        res = analytics.analyse(df, validate(df)["issues"])
        self.assertIsNone(res["trend"])
        self.assertIsNone(res["geography"])
        self.assertIsNone(res["quality"]["by_enumerator"])
        self.assertEqual(res["categorical"]["colour"]["values"][0][:2], ["red", 2])
        self.assertEqual(res["numeric"]["size"]["median"], 2.0)
        self.assertTrue(res["findings"])

    def test_all_blank_column_does_not_crash(self):
        df = pd.DataFrame({"a": ["", "", ""], "b": ["x", "y", "z"]})
        res = analytics.analyse(df, validate(df)["issues"])
        self.assertNotIn("a", res["numeric"])
        self.assertNotIn("a", res["categorical"])


if __name__ == "__main__":
    unittest.main()

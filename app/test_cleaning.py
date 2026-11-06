"""Tests for suggested corrections and their application. Run:  python -m unittest discover tests -v"""
import unittest
from pathlib import Path

import pandas as pd

from fielddata import cleaning
from fielddata.validation import validate

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "survey_sample.csv"


def table(rows):
    return pd.DataFrame(rows).astype(str)


class CleaningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = pd.read_csv(SAMPLE, dtype=str, keep_default_na=False)
        cls.res = validate(cls.raw)
        cls.props = cleaning.propose(cls.raw, cls.res["issues"])

    def test_words_to_number(self):
        for text, n in [("twenty one", 21), ("thirty-five", 35), ("Seventeen", 17), ("forty", 40), (" sixty  two ", 62)]:
            self.assertEqual(cleaning.words_to_number(text), n)
        for text in ["N/K", "twenty twenty", "thirty zero", "one hundred", "", "abc def"]:
            self.assertIsNone(cleaning.words_to_number(text))

    def test_sample_proposals_by_rule(self):
        by = self.props.rule.value_counts().to_dict()
        self.assertEqual(by["remove_duplicate"], 15)
        self.assertEqual(by["swap_gps"], 8)
        self.assertEqual(by["standardise_category"], 25)
        self.assertEqual(by["number_words_to_digits"], 2)
        self.assertGreater(by["clear_invalid_value"], 20)
        self.assertEqual(set(self.props.confidence), {"high", "medium", "low"})

    def test_one_proposal_per_cell_and_none_on_deleted_rows(self):
        cells = self.props[self.props.action != "delete_row"]
        self.assertFalse(cells.duplicated(subset=["row", "column"]).any())
        deleted = set(self.props[self.props.action == "delete_row"].row)
        self.assertFalse(set(cells.row) & deleted)

    def test_missing_values_are_never_filled_in(self):
        self.assertFalse((self.props.old_value.eq("") & self.props.action.eq("replace") & self.props.rule.ne("trim_whitespace")).any())
        self.assertFalse((self.props.action.eq("replace") & self.props.new_value.eq("")).any())

    def test_apply_gender_swap_numberwords_and_duplicates(self):
        df = table([
            {"_id": "1", "gender": "male ", "respondent_age": "thirty five", "latitude": "3.4", "longitude": "6.5", "state": "Lagos"},
            {"_id": "2", "gender": "Female", "respondent_age": "30", "latitude": "6.5", "longitude": "3.4", "state": "Lagos"},
        ])
        issues = pd.DataFrame([
            {"row": 2, "record_id": "1", "enumerator": "", "column": "gender", "issue_type": "inconsistent_category", "severity": "warning", "value": "male ", "message": "x", "suggestion": ""},
            {"row": 2, "record_id": "1", "enumerator": "", "column": "respondent_age", "issue_type": "format_error", "severity": "error", "value": "thirty five", "message": "not a number", "suggestion": ""},
            {"row": 2, "record_id": "1", "enumerator": "", "column": "latitude", "issue_type": "invalid_gps", "severity": "error", "value": "", "message": "Latitude and longitude look swapped", "suggestion": ""},
        ])
        props = cleaning.propose(df, issues)
        self.assertEqual(sorted(props.rule), ["number_words_to_digits", "standardise_category", "swap_gps"])
        clean, log, skipped = cleaning.apply_corrections(df, props.to_dict("records"))
        row = clean.loc[0]
        self.assertEqual((row.gender, row.respondent_age, row.latitude, row.longitude), ("Male", "35", "6.5", "3.4"))
        self.assertEqual(skipped, 0)
        self.assertEqual(len(log), 3)
        self.assertEqual(df.at[0, "gender"], "male ")                     # the original table is untouched

    def test_stale_proposal_is_skipped_not_forced(self):
        df = table([{"_id": "1", "gender": "m"}])
        prop = {"row": 2, "record_id": "1", "enumerator": "", "column": "gender", "column2": "", "action": "replace",
                "old_value": "m", "new_value": "Male", "rule": "standardise_category", "confidence": "high", "reason": "r"}
        df.at[0, "gender"] = "Female"                                      # someone changed it meanwhile
        clean, log, skipped = cleaning.apply_corrections(df, [prop])
        self.assertEqual((clean.at[0, "gender"], skipped, len(log)), ("Female", 1, 0))

    def test_deleted_rows_keep_original_row_numbers(self):
        accepted = self.props[self.props.rule == "remove_duplicate"].to_dict("records")
        clean, log, _ = cleaning.apply_corrections(self.raw, accepted)
        self.assertEqual(len(clean), len(self.raw) - 15)
        again = validate(clean)["issues"]
        self.assertEqual(len(again[again.issue_type == "duplicate_record"]), 0)
        self.assertTrue(set(again.row) <= set(range(2, len(self.raw) + 2)))   # row numbers still match the original file
        self.assertEqual(log.action.eq("delete_row").sum(), 15)

    def test_accepting_everything_improves_the_score_and_is_consistent(self):
        clean, log, skipped = cleaning.apply_corrections(self.raw, self.props.to_dict("records"))
        after = validate(clean)
        self.assertEqual(skipped, 0)
        self.assertGreater(after["summary"]["quality_score"], self.res["summary"]["quality_score"])
        self.assertEqual(after["issues"].query("issue_type == 'duplicate_record'").shape[0], 0)
        self.assertEqual(after["issues"].query("issue_type == 'inconsistent_category'").shape[0], 0)
        self.assertEqual(after["issues"].query("issue_type == 'invalid_gps' and message.str.contains('swapped')").shape[0], 0)

    def test_nothing_accepted_changes_nothing(self):
        clean, log, skipped = cleaning.apply_corrections(self.raw, [])
        pd.testing.assert_frame_equal(clean, self.raw)
        self.assertEqual((len(log), skipped), (0, 0))

    def test_spreadsheet_safety(self):
        for bad in ["=HYPERLINK(\"http://x\")", "@SUM(A1)", "+cmd|' /C calc'!A0", "-2+3+cmd", "\t=1+1"]:
            self.assertTrue(cleaning.safe_cell(bad).startswith("'"), bad)
        for fine in ["-5", "+2348031234567", "+234 803 123 4567", "0803 123 4567", "Kano", "", "-12.5", "(080) 123-4567", "3.14"]:
            self.assertEqual(cleaning.safe_cell(fine), fine)
        df = pd.DataFrame({"a": ["=1+1", "ok"]})
        self.assertEqual(cleaning.safe_for_spreadsheet(df)["a"].tolist(), ["'=1+1", "ok"])


if __name__ == "__main__":
    unittest.main()

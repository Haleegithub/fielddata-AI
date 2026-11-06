"""Tests for generating, reviewing, applying and exporting corrections. Run:  python -m unittest discover tests -v"""
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from app import cleaning_service as cs
from app import datasets, db, services
from app.services import ServiceError

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "survey_sample.csv"


class CleaningServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        services._failed.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name) / "uploads"
        self.a = services.register(self.conn, "A", "a@example.org", "correct horse")
        self.b = services.register(self.conn, "B", "b@example.org", "correct horse")
        self.pa = services.create_project(self.conn, self.a["id"], "Project")
        self.csv = SAMPLE.read_bytes()
        self.ds = datasets.save_dataset(self.conn, self.a["id"], self.pa["id"], "survey.csv", self.csv, self.storage)
        self.uid, self.did = self.a["id"], self.ds["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def code(self, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as ctx:
            fn(*args, **kwargs)
        return ctx.exception.status

    def generate(self):
        return cs.generate(self.conn, self.uid, self.did, self.storage)

    def accept_good(self):
        for conf in ("high", "medium"):
            cs.review(self.conn, self.uid, self.did, "accepted", confidence=conf)

    # ---- generate ------------------------------------------------------------------------------------------
    def test_generate_runs_validation_itself_and_counts_match(self):
        summary = self.generate()
        self.assertEqual(summary["by_rule"]["remove_duplicate"]["pending"], 15)
        self.assertEqual(summary["by_rule"]["swap_gps"]["confidence"], "high")
        self.assertEqual(summary["by_status"]["accepted"], 0)                       # nothing is accepted automatically
        self.assertEqual(summary["total"], sum(summary["by_status"].values()))
        self.assertTrue(datasets._path(self.storage, self.did, "_issues").exists())

    def test_regenerate_resets_reviews_and_old_results(self):
        self.generate()
        self.accept_good()
        cs.apply(self.conn, self.uid, self.did, self.storage)
        again = self.generate()
        self.assertEqual(again["by_status"]["accepted"], 0)
        self.assertEqual(cs.get_cleaning(self.conn, self.uid, self.did), {"ran": False})
        self.assertFalse(datasets._path(self.storage, self.did, "_clean").exists())

    # ---- review --------------------------------------------------------------------------------------------
    def test_review_by_ids_rule_and_confidence(self):
        self.generate()
        rows = cs.list_corrections(self.conn, self.uid, self.did)["rows"]
        out = cs.review(self.conn, self.uid, self.did, "accepted", ids=[rows[0]["id"], rows[1]["id"]])
        self.assertEqual(out["updated"], 2)
        out = cs.review(self.conn, self.uid, self.did, "rejected", rule="remove_duplicate")
        self.assertEqual(out["updated"], 15)
        out = cs.review(self.conn, self.uid, self.did, "accepted", confidence="high")
        self.assertGreater(out["updated"], 0)
        self.assertEqual(out["summary"]["by_rule"]["remove_duplicate"]["rejected"], 15)

    def test_review_validation(self):
        self.generate()
        self.assertEqual(self.code(cs.review, self.conn, self.uid, self.did, "approved", rule="swap_gps"), 422)
        self.assertEqual(self.code(cs.review, self.conn, self.uid, self.did, "accepted"), 422)
        self.assertEqual(self.code(cs.review, self.conn, self.uid, self.did, "accepted", rule="made_up"), 422)
        self.assertEqual(self.code(cs.review, self.conn, self.uid, self.did, "accepted", confidence="certain"), 422)
        self.assertEqual(self.code(cs.review, self.conn, self.uid, self.did, "accepted", ids=list(range(cs.MAX_IDS + 1))), 422)

    def test_review_cannot_touch_another_datasets_corrections(self):
        self.generate()
        other = datasets.save_dataset(self.conn, self.uid, self.pa["id"], "two.csv", self.csv, self.storage)
        cs.generate(self.conn, self.uid, other["id"], self.storage)
        mine = cs.list_corrections(self.conn, self.uid, self.did)["rows"][0]["id"]
        out = cs.review(self.conn, self.uid, other["id"], "accepted", ids=[mine])
        self.assertEqual(out["updated"], 0)
        self.assertEqual(cs.list_corrections(self.conn, self.uid, self.did, status="accepted")["total"], 0)

    def test_list_filters_and_pages(self):
        self.generate()
        d = cs.list_corrections(self.conn, self.uid, self.did, rule="remove_duplicate", page_size=10)
        self.assertEqual((d["total"], d["pages"], len(d["rows"])), (15, 2, 10))
        self.assertEqual(cs.list_corrections(self.conn, self.uid, self.did, status="accepted")["total"], 0)
        self.assertEqual(cs.list_corrections(self.conn, self.uid, self.did, page=999)["page"],
                         cs.list_corrections(self.conn, self.uid, self.did)["pages"])
        self.assertEqual(self.code(cs.list_corrections, self.conn, self.uid, self.did, status="bogus"), 422)
        self.assertEqual(self.code(cs.list_corrections, self.conn, self.uid, self.did, rule="bogus"), 422)
        first = d["rows"][0]
        self.assertTrue({"row", "column", "old_value", "new_value", "rule_label", "confidence", "status"} <= set(first))

    # ---- apply ---------------------------------------------------------------------------------------------
    def test_apply_needs_generation_and_acceptance(self):
        self.assertEqual(self.code(cs.apply, self.conn, self.uid, self.did, self.storage), 422)
        self.generate()
        self.assertEqual(self.code(cs.apply, self.conn, self.uid, self.did, self.storage), 422)

    def test_apply_creates_clean_copy_and_leaves_original_alone(self):
        before_bytes = datasets._path(self.storage, self.did).read_bytes()
        self.generate()
        self.accept_good()
        rep = cs.apply(self.conn, self.uid, self.did, self.storage)
        self.assertEqual(rep["records_removed"], 15)
        self.assertEqual(rep["records_after"], rep["records_before"] - 15)
        self.assertGreater(rep["score_after"], rep["score_before"])
        self.assertEqual(rep["skipped"], 0)
        self.assertEqual(rep["changes_applied"] + rep["records_removed"], rep["accepted"])
        self.assertEqual(datasets._path(self.storage, self.did).read_bytes(), before_bytes)        # original untouched
        clean = pd.read_csv(datasets._path(self.storage, self.did, "_clean"), dtype=str, keep_default_na=False)
        self.assertEqual(len(clean), rep["records_after"])
        self.assertTrue(set(clean.gender) <= {"Male", "Female", "", "unknown", "X", "yes"})
        log = pd.read_csv(datasets._path(self.storage, self.did, "_changes"), dtype=str, keep_default_na=False)
        self.assertEqual(len(log), rep["accepted"])
        follow = pd.read_csv(datasets._path(self.storage, self.did, "_followup"), dtype=str, keep_default_na=False)
        self.assertTrue((follow.severity == "error").all())
        self.assertEqual(cs.get_cleaning(self.conn, self.uid, self.did)["score_after"], rep["score_after"])

    def test_rejected_corrections_are_not_applied(self):
        self.generate()
        cs.review(self.conn, self.uid, self.did, "accepted", rule="swap_gps")
        cs.review(self.conn, self.uid, self.did, "rejected", rule="remove_duplicate")
        rep = cs.apply(self.conn, self.uid, self.did, self.storage)
        self.assertEqual((rep["records_removed"], rep["changes_applied"]), (0, 8))
        self.assertEqual(rep["records_after"], rep["records_before"])

    # ---- downloads -----------------------------------------------------------------------------------------
    def test_exports(self):
        self.assertEqual(self.code(cs.export, self.conn, self.uid, self.did, self.storage, "cleaned", "csv"), 404)
        self.generate()
        self.accept_good()
        cs.apply(self.conn, self.uid, self.did, self.storage)
        for what in ("original", "cleaned", "changes", "followup"):
            name, ctype, data = cs.export(self.conn, self.uid, self.did, self.storage, what, "csv")
            self.assertTrue(name.endswith(".csv") and ctype == "text/csv")
            self.assertGreater(len(pd.read_csv(io.BytesIO(data), dtype=str)), 0)
        name, ctype, data = cs.export(self.conn, self.uid, self.did, self.storage, "cleaned", "xlsx")
        self.assertTrue(name == "survey_cleaned.xlsx" and "spreadsheetml" in ctype)
        self.assertEqual(load_workbook(io.BytesIO(data)).active.max_row, 615 - 15 + 1)
        self.assertEqual(self.code(cs.export, self.conn, self.uid, self.did, self.storage, "secrets", "csv"), 422)
        self.assertEqual(self.code(cs.export, self.conn, self.uid, self.did, self.storage, "cleaned", "pdf"), 422)

    def test_exports_cannot_run_formulas(self):
        evil = b"name,note\nAda,=HYPERLINK(\"http://example.org\")\nBo,@SUM(A1)\nCy,fine\n"
        d = datasets.save_dataset(self.conn, self.uid, self.pa["id"], "evil.csv", evil, self.storage)
        _, _, csv_data = cs.export(self.conn, self.uid, d["id"], self.storage, "original", "csv")
        text = csv_data.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'@SUM", text)
        _, _, xlsx = cs.export(self.conn, self.uid, d["id"], self.storage, "original", "xlsx")
        cell = load_workbook(io.BytesIO(xlsx)).active["B2"]
        self.assertEqual(cell.data_type, "s")                                       # stored as text, not a formula
        self.assertTrue(str(cell.value).startswith("'="))

    # ---- ownership and deleting --------------------------------------------------------------------------------
    def test_other_users_get_404_everywhere(self):
        self.generate()
        self.accept_good()
        cs.apply(self.conn, self.uid, self.did, self.storage)
        b = self.b["id"]
        for fn, args in [(cs.generate, (self.storage,)), (cs.list_corrections, ()), (cs.review, ("accepted",)),
                         (cs.apply, (self.storage,)), (cs.get_cleaning, ()), (cs.export, (self.storage, "cleaned", "csv"))]:
            kwargs = {"confidence": "high"} if fn is cs.review else {}
            self.assertEqual(self.code(fn, self.conn, b, self.did, *args, **kwargs), 404, fn.__name__)

    def test_deleting_a_dataset_removes_all_its_cleaning_files_and_rows(self):
        self.generate()
        self.accept_good()
        cs.apply(self.conn, self.uid, self.did, self.storage)
        self.assertEqual(len(list(self.storage.iterdir())), 5)
        datasets.delete_dataset(self.conn, self.uid, self.did, self.storage)
        self.assertEqual(list(self.storage.iterdir()), [])
        for table in ("corrections", "cleaning_runs", "validation_runs"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_deleting_a_project_removes_cleaning_files(self):
        self.generate()
        self.accept_good()
        cs.apply(self.conn, self.uid, self.did, self.storage)
        datasets.remove_project(self.conn, self.uid, self.pa["id"], self.storage)
        self.assertEqual(list(self.storage.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

"""Tests for dataset analytics and project summaries. Run:  python -m unittest discover tests -v"""
import tempfile
import unittest
from pathlib import Path

from app import analytics_service as an
from app import cleaning_service as cs
from app import datasets, db, services
from app.services import ServiceError

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "survey_sample.csv"


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        services._failed.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name) / "uploads"
        self.a = services.register(self.conn, "A", "a@example.org", "correct horse")
        self.b = services.register(self.conn, "B", "b@example.org", "correct horse")
        self.p = services.create_project(self.conn, self.a["id"], "P")
        self.ds = datasets.save_dataset(self.conn, self.a["id"], self.p["id"], "survey.csv", SAMPLE.read_bytes(), self.storage)
        self.uid, self.did = self.a["id"], self.ds["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def code(self, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as ctx:
            fn(*args, **kwargs)
        return ctx.exception.status

    def clean(self):
        cs.generate(self.conn, self.uid, self.did, self.storage)
        for conf in ("high", "medium"):
            cs.review(self.conn, self.uid, self.did, "accepted", confidence=conf)
        return cs.apply(self.conn, self.uid, self.did, self.storage)

    def test_original_runs_the_check_itself(self):
        r = an.dataset_analytics(self.conn, self.uid, self.did, self.storage)
        self.assertEqual((r["source"], r["cleaned_available"], r["dataset"]), ("original", False, "survey.csv"))
        self.assertEqual(r["overview"]["records_total"], 615)
        self.assertTrue(datasets._path(self.storage, self.did, "_issues").exists())

    def test_cleaned_needs_cleaning_first(self):
        self.assertEqual(self.code(an.dataset_analytics, self.conn, self.uid, self.did, self.storage, "cleaned"), 404)

    def test_cleaned_source_differs_from_original(self):
        self.clean()
        orig = an.dataset_analytics(self.conn, self.uid, self.did, self.storage, "original")
        clean = an.dataset_analytics(self.conn, self.uid, self.did, self.storage, "cleaned")
        self.assertTrue(clean["cleaned_available"])
        self.assertEqual((clean["overview"]["records_total"], clean["overview"]["duplicates_excluded"]), (600, 0))
        self.assertEqual(orig["overview"]["duplicates_excluded"], 15)
        self.assertLess(clean["overview"]["records_with_errors"], orig["overview"]["records_with_errors"])
        g = {v[0] for v in clean["categorical"]["gender"]["values"]}
        self.assertEqual(g & {"M", "F", "male ", "FEMALE", "m", "f"}, set())       # spellings were standardised
        self.assertGreater(clean["geography"]["gps_valid_records"], orig["geography"]["gps_valid_records"])   # swapped GPS fixed

    def test_validation_and_ownership(self):
        self.assertEqual(self.code(an.dataset_analytics, self.conn, self.uid, self.did, self.storage, "bogus"), 422)
        self.assertEqual(self.code(an.dataset_analytics, self.conn, self.b["id"], self.did, self.storage), 404)
        self.assertEqual(self.code(an.project_summary, self.conn, self.b["id"], self.p["id"]), 404)

    def test_project_summary(self):
        empty = an.project_summary(self.conn, self.uid, self.p["id"])
        self.assertEqual((empty["totals"]["datasets"], empty["totals"]["validated"], empty["totals"]["average_quality_score"]), (1, 0, None))
        datasets.run_validation(self.conn, self.uid, self.did, self.storage)
        self.clean()
        s = an.project_summary(self.conn, self.uid, self.p["id"])
        item = s["datasets"][0]
        self.assertEqual((item["quality_score"], item["rows"]), (73.2, 615))
        self.assertGreater(item["cleaned_score"], item["quality_score"])
        self.assertEqual(item["records_after_cleaning"], 600)
        self.assertEqual((s["totals"]["validated"], s["totals"]["cleaned"], s["totals"]["average_quality_score"]), (1, 1, 73.2))

    def test_project_summary_only_counts_that_projects_datasets(self):
        other = services.create_project(self.conn, self.uid, "Other")
        datasets.save_dataset(self.conn, self.uid, other["id"], "x.csv", b"a,b\n1,2\n3,4\n", self.storage)
        self.assertEqual(an.project_summary(self.conn, self.uid, other["id"])["totals"]["records"], 2)
        self.assertEqual(an.project_summary(self.conn, self.uid, self.p["id"])["totals"]["records"], 615)


if __name__ == "__main__":
    unittest.main()
